//! In-process D3D12 texture -> NVENC H.264 path (Windows only).
//!
//! WGPU owns a bounded pool of rendered textures. The encoder transitions a
//! batch to COMMON on WGPU's D3D12 queue, submits them to NVENC with explicit
//! input/output fence points, retrieves only compressed H.264 packets, then
//! returns the textures to WGPU's tracked RENDER_TARGET state. No uncompressed
//! video frame is mapped or copied to host memory.

use std::ffi::{c_void, CStr};
use std::mem::{self, ManuallyDrop};
use std::ptr;

use mini_nvcodec::nvenc_sys::*;
use mini_nvcodec::{try_init_nvenc, NvencApiVersion, API_VERSION_LADDER};
use windows::core::{Interface, PCWSTR};
use windows::Win32::Foundation::{CloseHandle, HANDLE, WAIT_OBJECT_0};
use windows::Win32::Graphics::Direct3D12::*;
use windows::Win32::Graphics::Dxgi::Common::{DXGI_FORMAT_UNKNOWN, DXGI_SAMPLE_DESC};
use windows::Win32::System::Threading::{CreateEventW, WaitForSingleObject, INFINITE};

pub struct D3d12Nvenc {
    api: &'static mini_nvcodec::NvencApi,
    session: *mut c_void,
    version: NvencApiVersion,
    device: ID3D12Device,
    queue: ID3D12CommandQueue,
    allocator: ID3D12CommandAllocator,
    command_list: ID3D12GraphicsCommandList,
    sync_fence: ID3D12Fence,
    encode_fence: ID3D12Fence,
    fence_event: HANDLE,
    fence_value: u64,
    next_output_fence_value: u64,
    finished: bool,
    output_capacity: u32,
    input_slots: Vec<RegisteredInput>,
    output_slots: Vec<RegisteredOutput>,
    width: u32,
    height: u32,
}

struct RegisteredInput {
    resource: ID3D12Resource,
    registered: NV_ENC_REGISTERED_PTR,
    mapped: NV_ENC_INPUT_PTR,
    format: NV_ENC_BUFFER_FORMAT,
    info: NV_ENC_INPUT_RESOURCE_D3D12,
}

struct RegisteredOutput {
    _resource: ID3D12Resource,
    registered: NV_ENC_REGISTERED_PTR,
    mapped: NV_ENC_INPUT_PTR,
    info: NV_ENC_OUTPUT_RESOURCE_D3D12,
}

impl D3d12Nvenc {
    pub fn new(
        device: &wgpu::Device,
        width: u32,
        height: u32,
        fps: u32,
        slot_count: usize,
    ) -> Result<Self, String> {
        if !cfg!(windows) {
            return Err("Direct3D 12 NVENC is only available on Windows".into());
        }
        let api = try_init_nvenc().ok_or_else(|| {
            "NVIDIA NVENC API is unavailable; install a current NVIDIA driver".to_owned()
        })?;
        let hal_device = unsafe { device.as_hal::<wgpu::hal::api::Dx12>() }
            .ok_or_else(|| "WGPU did not create a Direct3D 12 device".to_owned())?;
        let raw_device = hal_device.raw_device().clone();
        let raw_queue = hal_device.raw_queue().clone();

        let (session, version) = open_session(api, &raw_device)?;
        let mut preset: NV_ENC_PRESET_CONFIG = unsafe { mem::zeroed() };
        preset.version = version.preset_config_ver();
        preset.presetCfg.version = version.config_ver();
        check_nvenc(
            api,
            session,
            unsafe {
                (api.nvEncGetEncodePresetConfigEx)(
                    session,
                    mini_nvcodec::guids::NV_ENC_CODEC_H264_GUID,
                    mini_nvcodec::guids::NV_ENC_PRESET_P4_GUID,
                    NV_ENC_TUNING_INFO::HIGH_QUALITY,
                    &mut preset,
                )
            },
            "get H.264 P4 preset",
        )?;

        preset.presetCfg.gopLength = (fps.saturating_mul(2)).max(1);
        preset.presetCfg.frameIntervalP = 1;
        preset.presetCfg.rcParams.version = version.rc_params_ver();
        preset.presetCfg.rcParams.rateControlMode = NV_ENC_PARAMS_RC_MODE::VBR;
        preset.presetCfg.rcParams.averageBitRate = 8_000_000;
        preset.presetCfg.rcParams.maxBitRate = 12_000_000;

        let mut init: NV_ENC_INITIALIZE_PARAMS = unsafe { mem::zeroed() };
        init.version = version.initialize_params_ver();
        init.encodeGUID = mini_nvcodec::guids::NV_ENC_CODEC_H264_GUID;
        init.presetGUID = mini_nvcodec::guids::NV_ENC_PRESET_P4_GUID;
        init.encodeWidth = width;
        init.encodeHeight = height;
        init.maxEncodeWidth = width;
        init.maxEncodeHeight = height;
        init.darWidth = width;
        init.darHeight = height;
        init.frameRateNum = fps;
        init.frameRateDen = 1;
        init.enableEncodeAsync = 0;
        init.enablePTD = 1;
        init.tuningInfo = NV_ENC_TUNING_INFO::HIGH_QUALITY;
        init.bufferFormat = NV_ENC_BUFFER_FORMAT::ARGB;
        init.encodeConfig = &mut preset.presetCfg;
        check_nvenc(
            api,
            session,
            unsafe { (api.nvEncInitializeEncoder)(session, &mut init) },
            "initialize H.264 encoder",
        )?;

        let allocator: ID3D12CommandAllocator = unsafe {
            raw_device.CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT)
        }
        .map_err(|e| format!("create D3D12 command allocator: {e}"))?;
        let command_list: ID3D12GraphicsCommandList = unsafe {
            raw_device.CreateCommandList::<_, _, ID3D12GraphicsCommandList>(
                0,
                D3D12_COMMAND_LIST_TYPE_DIRECT,
                &allocator,
                None,
            )
        }
        .map_err(|e| format!("create D3D12 command list: {e}"))?;
        unsafe { command_list.Close() }
            .map_err(|e| format!("close initial D3D12 command list: {e}"))?;

        let sync_fence: ID3D12Fence = unsafe {
            raw_device.CreateFence(0, D3D12_FENCE_FLAG_NONE)
        }
        .map_err(|e| format!("create D3D12 render fence: {e}"))?;
        let encode_fence: ID3D12Fence = unsafe {
            raw_device.CreateFence(0, D3D12_FENCE_FLAG_NONE)
        }
        .map_err(|e| format!("create D3D12 NVENC fence: {e}"))?;
        let fence_event = unsafe { CreateEventW(None, false, false, PCWSTR::null()) }
            .map_err(|e| format!("create D3D12 fence event: {e}"))?;

        // NVIDIA's guide recommends about twice the input YUV420 payload.
        let output_capacity = width
            .checked_mul(height)
            .and_then(|pixels| pixels.checked_mul(3))
            .ok_or_else(|| "NVENC bitstream buffer size overflow".to_owned())?;
        let mut output_slots = Vec::with_capacity(slot_count);
        for _ in 0..slot_count {
            let resource = create_readback_buffer(&raw_device, u64::from(output_capacity))?;
            let mut registration: NV_ENC_REGISTER_RESOURCE = unsafe { mem::zeroed() };
            registration.version = version.register_resource_ver();
            registration.resourceType = NV_ENC_INPUT_RESOURCE_TYPE::DIRECTX;
            registration.width = output_capacity;
            registration.height = 1;
            registration.pitch = 0;
            registration.resourceToRegister = Interface::as_raw(&resource) as *mut c_void;
            registration.bufferFormat = NV_ENC_BUFFER_FORMAT::U8;
            registration.bufferUsage = NV_ENC_BUFFER_USAGE::OUTPUT_BITSTREAM;
            check_nvenc(
                api,
                session,
                unsafe { (api.nvEncRegisterResource)(session, &mut registration) },
                "register D3D12 NVENC bitstream buffer",
            )?;
            let registered = registration.registeredResource;
            let mut map: NV_ENC_MAP_INPUT_RESOURCE = unsafe { mem::zeroed() };
            map.version = version.map_input_resource_ver();
            map.registeredResource = registered;
            check_nvenc(
                api,
                session,
                unsafe { (api.nvEncMapInputResource)(session, &mut map) },
                "map D3D12 NVENC bitstream buffer",
            )?;
            output_slots.push(RegisteredOutput {
                _resource: resource,
                registered,
                mapped: map.mappedResource,
                info: NV_ENC_OUTPUT_RESOURCE_D3D12 {
                    version: version.struct_version(1),
                    reserved: 0,
                    pOutputBuffer: map.mappedResource,
                    outputFencePoint: NV_ENC_FENCE_POINT_D3D12 {
                        version: version.struct_version(1),
                        reserved: 0,
                        pFence: Interface::as_raw(&encode_fence) as *mut c_void,
                        waitValue: 0,
                        signalValue: 0,
                        bit_fields: 2,
                        reserved1: [0; 7],
                    },
                    reserved1: [0; 16],
                    reserved2: [ptr::null_mut(); 16],
                },
            });
        }

        eprintln!(
            "RUST_RENDER_NVENC: D3D12 zero-copy H.264 session opened ({width}x{height}@{fps})"
        );
        Ok(Self {
            api,
            session,
            version,
            device: raw_device,
            queue: raw_queue,
            allocator,
            command_list,
            sync_fence,
            encode_fence,
            fence_event,
            fence_value: 0,
            next_output_fence_value: 0,
            finished: false,
            output_capacity,
            input_slots: Vec::new(),
            output_slots,
            width,
            height,
        })
    }

    pub fn register_textures(&mut self, textures: &[wgpu::Texture]) -> Result<(), String> {
        for texture in textures {
            if texture.width() != self.width || texture.height() != self.height {
                return Err("NVENC input texture dimensions changed during a render".into());
            }
            let hal_texture = unsafe { texture.as_hal::<wgpu::hal::api::Dx12>() }
                .ok_or_else(|| "WGPU output texture is not backed by Direct3D 12".to_owned())?;
            let resource = unsafe { hal_texture.raw_resource() }.clone();
            let mut registration: NV_ENC_REGISTER_RESOURCE = unsafe { mem::zeroed() };
            registration.version = self.version.register_resource_ver();
            registration.resourceType = NV_ENC_INPUT_RESOURCE_TYPE::DIRECTX;
            registration.width = self.width;
            registration.height = self.height;
            registration.pitch = 0;
            registration.resourceToRegister = Interface::as_raw(&resource) as *mut c_void;
            registration.bufferFormat = NV_ENC_BUFFER_FORMAT::ARGB;
            registration.bufferUsage = NV_ENC_BUFFER_USAGE::INPUT_IMAGE;
            check_nvenc(
                self.api,
                self.session,
                unsafe { (self.api.nvEncRegisterResource)(self.session, &mut registration) },
                "register WGPU D3D12 render target",
            )?;

            let mut map: NV_ENC_MAP_INPUT_RESOURCE = unsafe { mem::zeroed() };
            map.version = self.version.map_input_resource_ver();
            map.registeredResource = registration.registeredResource;
            if let Err(error) = check_nvenc(
                self.api,
                self.session,
                unsafe { (self.api.nvEncMapInputResource)(self.session, &mut map) },
                "map WGPU D3D12 render target",
            ) {
                let _ = unsafe { (self.api.nvEncUnregisterResource)(self.session, registration.registeredResource) };
                return Err(error);
            }
            self.input_slots.push(RegisteredInput {
                resource,
                registered: registration.registeredResource,
                mapped: map.mappedResource,
                format: map.mappedBufferFmt,
                info: NV_ENC_INPUT_RESOURCE_D3D12 {
                    version: self.version.struct_version(1),
                    reserved: 0,
                    pInputBuffer: map.mappedResource,
                    inputFencePoint: NV_ENC_FENCE_POINT_D3D12 {
                        version: self.version.struct_version(1),
                        reserved: 0,
                        pFence: Interface::as_raw(&self.sync_fence) as *mut c_void,
                        waitValue: 0,
                        signalValue: 0,
                        bit_fields: 1,
                        reserved1: [0; 7],
                    },
                    reserved1: [0; 16],
                    reserved2: [ptr::null_mut(); 16],
                },
            });
        }
        Ok(())
    }

    /// Encode a rendered WGPU texture without mapping/copying its pixels to
    /// host memory. Only the compressed H.264 bitstream is copied to CPU RAM.
    pub fn encode_batch(&mut self, frames: &[(usize, u64)]) -> Result<Vec<Vec<u8>>, String> {
        if frames.is_empty() || frames.len() > self.output_slots.len() {
            return Err("NVENC batch size is outside the registered output-slot pool".into());
        }
        let mut resources = Vec::with_capacity(frames.len());
        for (slot, _) in frames {
            let input = self
                .input_slots
                .get(*slot)
                .ok_or_else(|| format!("NVENC texture slot {slot} is not registered"))?;
            resources.push(input.resource.clone());
        }

        // WGPU's command list has finished rendering the batch. Move every
        // input to COMMON in one D3D12 submission, then release one fence for
        // the whole NVENC batch.
        self.record_transitions(&resources, D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COMMON)?;
        self.fence_value += 1;
        unsafe { self.queue.Signal(&self.sync_fence, self.fence_value) }
            .map_err(|e| format!("signal WGPU-to-NVENC batch fence: {e}"))?;

        let mut pictures = Vec::with_capacity(frames.len());
        for (batch_slot, (input_slot, frame_index)) in frames.iter().copied().enumerate() {
            let input = &mut self.input_slots[input_slot];
            input.info.pInputBuffer = input.mapped;
            input.info.inputFencePoint.waitValue = self.fence_value;
            let output = &mut self.output_slots[batch_slot];
            self.next_output_fence_value += 1;
            output.info.outputFencePoint.signalValue = self.next_output_fence_value;
            let mut picture: NV_ENC_PIC_PARAMS = unsafe { mem::zeroed() };
            picture.version = self.version.pic_params_ver();
            picture.inputWidth = self.width;
            picture.inputHeight = self.height;
            picture.inputPitch = 0;
            picture.inputTimeStamp = frame_index;
            picture.inputDuration = 1;
            picture.inputBuffer = &mut input.info as *mut NV_ENC_INPUT_RESOURCE_D3D12 as *mut c_void;
            picture.outputBitstream = &mut output.info as *mut NV_ENC_OUTPUT_RESOURCE_D3D12 as *mut c_void;
            picture.bufferFmt = input.format;
            picture.pictureStruct = NV_ENC_PIC_STRUCT::FRAME;
            if frame_index == 0 {
                picture.encodePicFlags = NV_ENC_PIC_FLAG_FORCEIDR | NV_ENC_PIC_FLAG_OUTPUT_SPSPPS;
            }
            check_nvenc(
                self.api,
                self.session,
                unsafe { (self.api.nvEncEncodePicture)(self.session, &mut picture) },
                "submit D3D12 texture batch to NVENC",
            )?;
            pictures.push(picture);
        }

        // The fence is monotonic; waiting for the last value retires all
        // earlier output buffers while NVENC and WGPU overlap their work.
        wait_for_fence(&self.encode_fence, self.next_output_fence_value, self.fence_event)?;
        let mut bitstreams = Vec::with_capacity(frames.len());
        for batch_slot in 0..frames.len() {
            let output = &mut self.output_slots[batch_slot];
            let mut lock: NV_ENC_LOCK_BITSTREAM = unsafe { mem::zeroed() };
            lock.version = self.version.lock_bitstream_ver();
            lock.outputBitstream = &mut output.info as *mut NV_ENC_OUTPUT_RESOURCE_D3D12 as *mut c_void;
            check_nvenc(
                self.api,
                self.session,
                unsafe { (self.api.nvEncLockBitstream)(self.session, &mut lock) },
                "lock D3D12 NVENC output",
            )?;
            if lock.bitstreamSizeInBytes == 0
                || lock.bitstreamSizeInBytes > self.output_capacity
                || lock.bitstreamBufferPtr.is_null()
            {
                let _ = unsafe { (self.api.nvEncUnlockBitstream)(self.session, lock.outputBitstream) };
                return Err(format!(
                    "NVENC returned an invalid compressed bitstream buffer ({} bytes)",
                    lock.bitstreamSizeInBytes
                ));
            }
            bitstreams.push(unsafe {
                std::slice::from_raw_parts(
                    lock.bitstreamBufferPtr.cast::<u8>(),
                    lock.bitstreamSizeInBytes as usize,
                )
            }
            .to_vec());
            check_nvenc(
                self.api,
                self.session,
                unsafe { (self.api.nvEncUnlockBitstream)(self.session, lock.outputBitstream) },
                "unlock D3D12 NVENC output",
            )?;
        }

        // Return all input textures to WGPU's tracked RENDER_TARGET state in
        // one batch, waiting once before those slots can be rendered again.
        self.record_transitions(&resources, D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_RENDER_TARGET)?;
        self.fence_value += 1;
        unsafe { self.queue.Signal(&self.sync_fence, self.fence_value) }
            .map_err(|e| format!("signal NVENC-to-WGPU batch fence: {e}"))?;
        wait_for_fence(&self.sync_fence, self.fence_value, self.fence_event)?;
        Ok(bitstreams)
    }

    pub fn finish(&mut self) -> Result<(), String> {
        if self.finished {
            return Ok(());
        }
        let mut eos: NV_ENC_PIC_PARAMS = unsafe { mem::zeroed() };
        eos.version = self.version.pic_params_ver();
        eos.encodePicFlags = NV_ENC_PIC_FLAG_EOS;
        check_nvenc(
            self.api,
            self.session,
            unsafe { (self.api.nvEncEncodePicture)(self.session, &mut eos) },
            "flush D3D12 NVENC encoder",
        )?;
        self.finished = true;
        Ok(())
    }

    fn record_transitions(
        &mut self,
        resources: &[ID3D12Resource],
        before: D3D12_RESOURCE_STATES,
        after: D3D12_RESOURCE_STATES,
    ) -> Result<(), String> {
        unsafe { self.allocator.Reset() }.map_err(|e| format!("reset D3D12 command allocator: {e}"))?;
        unsafe { self.command_list.Reset(&self.allocator, None) }
            .map_err(|e| format!("reset D3D12 command list: {e}"))?;
        let barriers: Vec<D3D12_RESOURCE_BARRIER> = resources
            .iter()
            .map(|resource| D3D12_RESOURCE_BARRIER {
                Type: D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
                Flags: D3D12_RESOURCE_BARRIER_FLAG_NONE,
                Anonymous: D3D12_RESOURCE_BARRIER_0 {
                    Transition: ManuallyDrop::new(D3D12_RESOURCE_TRANSITION_BARRIER {
                        pResource: ManuallyDrop::new(Some(resource.clone())),
                        Subresource: D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES,
                        StateBefore: before,
                        StateAfter: after,
                    }),
                },
            })
            .collect();
        unsafe { self.command_list.ResourceBarrier(&barriers) };
        unsafe { self.command_list.Close() }.map_err(|e| format!("close D3D12 transition list: {e}"))?;
        let list: ID3D12CommandList = self
            .command_list
            .cast()
            .map_err(|e| format!("cast D3D12 command list: {e}"))?;
        unsafe { self.queue.ExecuteCommandLists(&[Some(list)]) };
        Ok(())
    }
}

impl Drop for D3d12Nvenc {
    fn drop(&mut self) {
        unsafe {
            for slot in self.input_slots.drain(..) {
                let _ = (self.api.nvEncUnmapInputResource)(self.session, slot.mapped);
                let _ = (self.api.nvEncUnregisterResource)(self.session, slot.registered);
            }
            for slot in self.output_slots.drain(..) {
                let _ = (self.api.nvEncUnmapInputResource)(self.session, slot.mapped);
                let _ = (self.api.nvEncUnregisterResource)(self.session, slot.registered);
            }
            let _ = (self.api.nvEncDestroyEncoder)(self.session);
            let _ = CloseHandle(self.fence_event);
        }
        let _ = &self.device;
    }
}

fn open_session(
    api: &'static mini_nvcodec::NvencApi,
    device: &ID3D12Device,
) -> Result<(*mut c_void, NvencApiVersion), String> {
    for version in API_VERSION_LADDER {
        if !version.supported_by(api.max_supported_version) {
            continue;
        }
        let mut open: NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS = unsafe { mem::zeroed() };
        open.version = version.open_encode_session_ex_params_ver();
        open.deviceType = NV_ENC_DEVICE_TYPE::DIRECTX;
        open.device = Interface::as_raw(device) as *mut c_void;
        open.apiVersion = version.nvencapi_version();
        let mut session = ptr::null_mut();
        let status = unsafe { (api.nvEncOpenEncodeSessionEx)(&mut open, &mut session) };
        if status == NVENCSTATUS::SUCCESS {
            if session.is_null() {
                return Err("NVENC opened a null D3D12 session".into());
            }
            return Ok((session, version));
        }
        if status != NVENCSTATUS::ERR_INVALID_VERSION {
            return Err(format!("open D3D12 NVENC session failed: {status:?}"));
        }
    }
    Err("NVIDIA driver does not support a compatible NVENC API version".into())
}

fn create_readback_buffer(device: &ID3D12Device, bytes: u64) -> Result<ID3D12Resource, String> {
    let heap = D3D12_HEAP_PROPERTIES {
        Type: D3D12_HEAP_TYPE_READBACK,
        CPUPageProperty: D3D12_CPU_PAGE_PROPERTY_UNKNOWN,
        MemoryPoolPreference: D3D12_MEMORY_POOL_UNKNOWN,
        CreationNodeMask: 1,
        VisibleNodeMask: 1,
    };
    let desc = D3D12_RESOURCE_DESC {
        Dimension: D3D12_RESOURCE_DIMENSION_BUFFER,
        Alignment: 0,
        Width: bytes,
        Height: 1,
        DepthOrArraySize: 1,
        MipLevels: 1,
        Format: DXGI_FORMAT_UNKNOWN,
        SampleDesc: DXGI_SAMPLE_DESC { Count: 1, Quality: 0 },
        Layout: D3D12_TEXTURE_LAYOUT_ROW_MAJOR,
        Flags: D3D12_RESOURCE_FLAG_NONE,
    };
    let mut resource = None;
    unsafe {
        device.CreateCommittedResource(
            &heap,
            D3D12_HEAP_FLAG_NONE,
            &desc,
            D3D12_RESOURCE_STATE_COPY_DEST,
            None,
            &mut resource,
        )
    }
    .map_err(|e| format!("create NVENC D3D12 readback buffer: {e}"))?;
    resource.ok_or_else(|| "D3D12 returned a null bitstream readback buffer".into())
}

fn check_nvenc(
    api: &'static mini_nvcodec::NvencApi,
    session: *mut c_void,
    status: NVENCSTATUS,
    operation: &str,
) -> Result<(), String> {
    if status == NVENCSTATUS::SUCCESS {
        return Ok(());
    }
    let detail = unsafe {
        let pointer = (api.nvEncGetLastErrorString)(session);
        if pointer.is_null() {
            None
        } else {
            Some(CStr::from_ptr(pointer).to_string_lossy().into_owned())
        }
    }
    .unwrap_or_default();
    Err(format!("{operation} failed: {status:?} {detail}"))
}

fn wait_for_fence(fence: &ID3D12Fence, value: u64, event: HANDLE) -> Result<(), String> {
    if unsafe { fence.GetCompletedValue() } >= value {
        return Ok(());
    }
    unsafe { fence.SetEventOnCompletion(value, event) }
        .map_err(|e| format!("set D3D12 fence event: {e}"))?;
    let result = unsafe { WaitForSingleObject(event, INFINITE) };
    if result == WAIT_OBJECT_0 {
        Ok(())
    } else {
        Err(format!("D3D12 fence wait returned {result:?}"))
    }
}
