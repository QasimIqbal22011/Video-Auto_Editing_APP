use std::collections::HashMap;
use std::fs;
use std::io::{self, Write};
use std::path::Path;
use std::sync::mpsc;
use std::time::Duration;

use bytemuck::{Pod, Zeroable};
use image::imageops::FilterType;
use serde::Deserialize;

const RENDER_BATCH_FRAMES: usize = 8;

#[cfg(windows)]
mod nvenc_d3d12;

const SHADER: &str = r#"
struct Params {
    output_size: vec2<f32>,
    from_size: vec2<f32>,
    to_size: vec2<f32>,
    zoom_from: f32,
    zoom_to: f32,
    progress: f32,
    transition: u32,
    padding: vec2<f32>,
};

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@group(0) @binding(0) var from_image: texture_2d<f32>;
@group(0) @binding(1) var from_sampler: sampler;
@group(0) @binding(2) var to_image: texture_2d<f32>;
@group(0) @binding(3) var to_sampler: sampler;
@group(0) @binding(4) var<uniform> params: Params;

@vertex
fn vs_main(@builtin(vertex_index) index: u32) -> VertexOut {
    let positions = array<vec2<f32>, 3>(
        vec2<f32>(-1.0, -1.0),
        vec2<f32>( 3.0, -1.0),
        vec2<f32>(-1.0,  3.0)
    );
    var out: VertexOut;
    let p = positions[index];
    out.position = vec4<f32>(p, 0.0, 1.0);
    out.uv = vec2<f32>((p.x + 1.0) * 0.5, (1.0 - p.y) * 0.5);
    return out;
}

fn fitted_uv(uv: vec2<f32>, source_size: vec2<f32>, zoom: f32) -> vec2<f32> {
    let source_aspect = source_size.x / source_size.y;
    let output_aspect = params.output_size.x / params.output_size.y;
    var crop_uv = uv;
    if (source_aspect > output_aspect) {
        crop_uv.x = (uv.x - 0.5) * (output_aspect / source_aspect) + 0.5;
    } else {
        crop_uv.y = (uv.y - 0.5) * (source_aspect / output_aspect) + 0.5;
    }
    return clamp((crop_uv - vec2<f32>(0.5, 0.5)) / max(zoom, 0.001) + vec2<f32>(0.5, 0.5),
                 vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 1.0));
}

fn sample_from(uv: vec2<f32>) -> vec4<f32> {
    return textureSample(from_image, from_sampler,
                         fitted_uv(uv, params.from_size, params.zoom_from));
}

fn sample_to(uv: vec2<f32>) -> vec4<f32> {
    return textureSample(to_image, to_sampler,
                         fitted_uv(uv, params.to_size, params.zoom_to));
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let p = clamp(params.progress, 0.0, 1.0);
    let source_color = sample_from(in.uv);
    let incoming_color = sample_to(in.uv);
    var rgb: vec3<f32>;

    switch params.transition {
        case 1u, 2u: { // fade / dissolve
            rgb = mix(source_color.rgb * source_color.a, incoming_color.rgb * incoming_color.a, p);
        }
        case 3u: { // wipe left
            rgb = select(source_color.rgb * source_color.a, incoming_color.rgb * incoming_color.a, in.uv.x < p);
        }
        case 4u: { // incoming picture slides from left to right
            if (in.uv.x < p) {
                let shifted = vec2<f32>(clamp(in.uv.x + 1.0 - p, 0.0, 1.0), in.uv.y);
                let incoming = textureSample(to_image, to_sampler,
                    fitted_uv(shifted, params.to_size, params.zoom_to));
                rgb = incoming.rgb * incoming.a;
            } else {
                let shifted = vec2<f32>(clamp(in.uv.x - p, 0.0, 1.0), in.uv.y);
                let outgoing = textureSample(from_image, from_sampler,
                    fitted_uv(shifted, params.from_size, params.zoom_from));
                rgb = outgoing.rgb * outgoing.a;
            }
        }
        case 5u: { // circle open
            let d = distance(in.uv, vec2<f32>(0.5, 0.5));
            let radius = p * 0.70710678;
            rgb = select(source_color.rgb * source_color.a, incoming_color.rgb * incoming_color.a, d <= radius);
        }
        case 6u: { // fade through black
            let brightness = select(1.0 - 2.0 * p, 2.0 * p - 1.0, p >= 0.5);
            let base_color = select(source_color.rgb * source_color.a, incoming_color.rgb * incoming_color.a, p >= 0.5);
            rgb = base_color * max(brightness, 0.0);
        }
        default: {
            rgb = incoming_color.rgb * incoming_color.a;
        }
    }
    return vec4<f32>(rgb, 1.0);
}
"#;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Manifest {
    width: u32,
    height: u32,
    fps: u32,
    audio_duration: f64,
    zoom_type: String,
    zoom_amount: f32,
    transition_type: String,
    transition_duration: f64,
    #[serde(default)]
    use_hardware_accel: bool,
    images: Vec<ImageItem>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ImageItem {
    path: String,
    start_sec: f64,
    end_sec: f64,
    duration: f64,
}

#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct Params {
    output_size: [f32; 2],
    from_size: [f32; 2],
    to_size: [f32; 2],
    zoom_from: f32,
    zoom_to: f32,
    progress: f32,
    transition: u32,
    padding: [f32; 2],
}

struct GpuImage {
    _texture: wgpu::Texture,
    view: wgpu::TextureView,
    size: [f32; 2],
}

enum DirectEncoder {
    #[cfg(windows)]
    Nvenc(nvenc_d3d12::D3d12Nvenc),
    #[cfg(not(windows))]
    Disabled,
}

impl DirectEncoder {
    fn register_textures(&mut self, textures: &[wgpu::Texture]) -> Result<(), String> {
        #[cfg(windows)]
        {
            match self {
                Self::Nvenc(encoder) => encoder.register_textures(textures),
            }
        }
        #[cfg(not(windows))]
        {
            let _ = textures;
            Err("Direct3D 12 NVENC is only available on Windows".into())
        }
    }
    fn finish(&mut self) -> Result<(), String> {
        #[cfg(windows)]
        {
            match self {
                Self::Nvenc(encoder) => encoder.finish(),
            }
        }
        #[cfg(not(windows))]
        {
            Err("Direct3D 12 NVENC is only available on Windows".into())
        }
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("RUST_RENDER_ERROR: {error}");
        std::process::exit(1);
    }

}

fn run() -> Result<(), String> {
    let manifest_path = std::env::args_os()
        .nth(1)
        .ok_or_else(|| "expected a render manifest path".to_owned())?;
    let bytes = fs::read(&manifest_path).map_err(|e| format!("could not read manifest: {e}"))?;
    let manifest: Manifest =
        serde_json::from_slice(&bytes).map_err(|e| format!("invalid render manifest: {e}"))?;
    validate(&manifest)?;

    let backends = if cfg!(windows) {
        wgpu::Backends::DX12
    } else if cfg!(target_os = "macos") {
        wgpu::Backends::METAL
    } else {
        wgpu::Backends::VULKAN
    };
    let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
        backends,
        ..wgpu::InstanceDescriptor::new_without_display_handle()
    });
    let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
        power_preference: wgpu::PowerPreference::HighPerformance,
        force_fallback_adapter: false,
        ..Default::default()
    }))
    .map_err(|e| format!("no hardware adapter for {backends:?}: {e}"))?;
    let info = adapter.get_info();
    eprintln!(
        "RUST_RENDER_ADAPTER: {} ({:?}, {:?})",
        info.name, info.device_type, info.backend
    );
    let adapter_limits = adapter.limits();
    let limits = wgpu::Limits::default()
        .or_worse_values_from(&adapter_limits)
        .using_resolution(adapter_limits.clone());
    let max_texture_side = limits.max_texture_dimension_2d;
    let (device, queue) = pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor {
        label: Some("Video Helper frame renderer"),
        required_limits: limits,
        ..Default::default()
    }))
    .map_err(|e| format!("could not open GPU device: {e}"))?;

    if manifest.width > max_texture_side || manifest.height > max_texture_side {
        return Err(format!(
            "{}x{} exceeds this adapter's {}-pixel texture limit",
            manifest.width, manifest.height, max_texture_side
        ));
    }
    let direct_nvenc = cfg!(windows) && manifest.use_hardware_accel;
    render_frames(&manifest, &device, &queue, max_texture_side, direct_nvenc)
}

fn validate(manifest: &Manifest) -> Result<(), String> {
    if manifest.width == 0
        || manifest.height == 0
        || manifest.width > 8192
        || manifest.height > 8192
    {
        return Err("output dimensions must be between 1 and 8192".into());
    }
    if manifest.fps == 0 || manifest.fps > 120 {
        return Err("frame rate must be between 1 and 120".into());
    }
    if !manifest.audio_duration.is_finite() || manifest.audio_duration <= 0.0 {
        return Err("audio duration must be finite and positive".into());
    }
    if manifest.images.is_empty() {
        return Err("the timeline contains no images".into());
    }
    let mut previous_start = -1.0;
    for item in &manifest.images {
        if item.path.is_empty()
            || !Path::new(&item.path).is_file()
            || !item.start_sec.is_finite()
            || !item.end_sec.is_finite()
            || !item.duration.is_finite()
            || item.duration <= 0.0
            || item.start_sec < 0.0
            || item.start_sec < previous_start
        {
            return Err(format!("invalid image timeline item: {}", item.path));
        }
        previous_start = item.start_sec;
    }
    if !manifest.zoom_amount.is_finite() || !manifest.transition_duration.is_finite() {
        return Err("effect parameters must be finite".into());
    }
    Ok(())
}

fn transition_id(name: &str) -> u32 {
    match name.to_ascii_lowercase().as_str() {
        "fade" => 1,
        "dissolve" => 2,
        "wipeleft" => 3,
        "slideright" => 4,
        "circleopen" => 5,
        "fadeblack" => 6,
        _ => 0,
    }
}

fn render_frames(
    manifest: &Manifest,
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    max_texture_side: u32,
    direct_nvenc: bool,
) -> Result<(), String> {
    let mut direct_encoder = if direct_nvenc {
        #[cfg(windows)]
        {
            Some(DirectEncoder::Nvenc(nvenc_d3d12::D3d12Nvenc::new(
                device,
                manifest.width,
                manifest.height,
                manifest.fps,
                RENDER_BATCH_FRAMES,
            )?))
        }
        #[cfg(not(windows))]
        {
            Some(DirectEncoder::Disabled)
        }
    } else {
        None
    };
    let output_format = if direct_encoder.is_some() {
        wgpu::TextureFormat::Bgra8Unorm
    } else {
        wgpu::TextureFormat::Rgba8Unorm
    };
    let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("two-still transition inputs"),
        entries: &[
            texture_entry(0),
            sampler_entry(1),
            texture_entry(2),
            sampler_entry(3),
            wgpu::BindGroupLayoutEntry {
                binding: 4,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            },
        ],
    });
    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("frame scheduler transitions and zoom"),
        source: wgpu::ShaderSource::Wgsl(SHADER.into()),
    });
    let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("frame renderer pipeline layout"),
        bind_group_layouts: &[Some(&layout)],
        immediate_size: 0,
    });
    let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("frame renderer pipeline"),
        layout: Some(&pipeline_layout),
        vertex: wgpu::VertexState {
            module: &shader,
            entry_point: Some("vs_main"),
            compilation_options: Default::default(),
            buffers: &[],
        },
        fragment: Some(wgpu::FragmentState {
            module: &shader,
            entry_point: Some("fs_main"),
            compilation_options: Default::default(),
            targets: &[Some(wgpu::ColorTargetState {
                format: output_format,
                blend: None,
                write_mask: wgpu::ColorWrites::ALL,
            })],
        }),
        primitive: wgpu::PrimitiveState::default(),
        depth_stencil: None,
        multisample: wgpu::MultisampleState::default(),
        multiview_mask: None,
        cache: None,
    });
    let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
        label: Some("linear still-image sampler"),
        address_mode_u: wgpu::AddressMode::ClampToEdge,
        address_mode_v: wgpu::AddressMode::ClampToEdge,
        mag_filter: wgpu::FilterMode::Linear,
        min_filter: wgpu::FilterMode::Linear,
        ..Default::default()
    });
    // Render a small batch and map all of its readbacks together. This keeps
    // the WGPU queue busy without retaining an unbounded number of frames.
    const BATCH_FRAMES: usize = RENDER_BATCH_FRAMES;
    let uniforms: Vec<wgpu::Buffer> = (0..BATCH_FRAMES)
        .map(|_| {
            device.create_buffer(&wgpu::BufferDescriptor {
                label: Some("per-frame render parameters"),
                size: std::mem::size_of::<Params>() as u64,
                usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            })
        })
        .collect();
    let outputs: Vec<wgpu::Texture> = (0..BATCH_FRAMES)
        .map(|_| {
            device.create_texture(&wgpu::TextureDescriptor {
                label: Some("bounded output frame slot"),
                size: wgpu::Extent3d {
                    width: manifest.width,
                    height: manifest.height,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: output_format,
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
                view_formats: &[],
            })
        })
        .collect();
    let output_views: Vec<wgpu::TextureView> = outputs
        .iter()
        .map(|texture| texture.create_view(&wgpu::TextureViewDescriptor::default()))
        .collect();
    if let Some(encoder) = direct_encoder.as_mut() {
        encoder.register_textures(&outputs)?;
    }
    let padded_row = align_up(manifest.width * 4, wgpu::COPY_BYTES_PER_ROW_ALIGNMENT);
    let readbacks: Vec<wgpu::Buffer> = if direct_encoder.is_none() {
        (0..BATCH_FRAMES)
            .map(|_| {
                device.create_buffer(&wgpu::BufferDescriptor {
                    label: Some("bounded batched frame readback"),
                    size: u64::from(padded_row) * u64::from(manifest.height),
                    usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
                    mapped_at_creation: false,
                })
            })
            .collect()
    } else {
        Vec::new()
    };

    let frame_count = (manifest.audio_duration * f64::from(manifest.fps)).ceil() as u64;
    let mut loaded: HashMap<usize, GpuImage> = HashMap::with_capacity(2);
    let mut pair: Option<(usize, usize)> = None;
    let mut stdout = io::stdout().lock();
    let transition = transition_id(&manifest.transition_type);
    let zoom_amount = manifest.zoom_amount.clamp(0.0, 0.30);

    for batch_start in (0..frame_count).step_by(BATCH_FRAMES) {
        let batch_len = (frame_count - batch_start).min(BATCH_FRAMES as u64) as usize;
        let mut command_encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor {
            label: Some("render bounded frame batch"),
        });
        for batch_slot in 0..batch_len {
            let frame_index = batch_start + batch_slot as u64;
            let time = frame_index as f64 / f64::from(manifest.fps);
            let current = image_at(&manifest.images, time);
            let transition_duration = if current > 0 && transition != 0 {
                manifest
                    .transition_duration
                    .min(manifest.images[current - 1].duration / 2.0)
                    .min(manifest.images[current].duration / 2.0)
            } else {
                0.0
            };
            let in_transition = current > 0
                && transition_duration > 0.0
                && time < manifest.images[current].start_sec + transition_duration;
            let (from_index, to_index, kind, progress) = if in_transition {
                let p = ((time - manifest.images[current].start_sec) / transition_duration)
                    .clamp(0.0, 1.0) as f32;
                (current - 1, current, transition, p)
            } else {
                (current, current, 0, 1.0)
            };

            if pair != Some((from_index, to_index)) {
                loaded.retain(|index, _| *index == from_index || *index == to_index);
                if !loaded.contains_key(&from_index) {
                    loaded.insert(
                        from_index,
                        load_gpu_image(
                            device,
                            queue,
                            &manifest.images[from_index],
                            max_texture_side,
                        )?,
                    );
                }
                if !loaded.contains_key(&to_index) {
                    loaded.insert(
                        to_index,
                        load_gpu_image(
                            device,
                            queue,
                            &manifest.images[to_index],
                            max_texture_side,
                        )?,
                    );
                }
                pair = Some((from_index, to_index));
            }

            let from = &manifest.images[from_index];
            let to = &manifest.images[to_index];
            let zoom_at = |image: &ImageItem| {
                if manifest.zoom_type == "none" || zoom_amount == 0.0 {
                    return 1.0;
                }
                let local = ((time - image.start_sec) / image.duration).clamp(0.0, 1.0) as f32;
                if manifest.zoom_type == "zoom_out" {
                    1.0 + zoom_amount * (1.0 - local)
                } else {
                    1.0 + zoom_amount * local
                }
            };
            let params = Params {
                output_size: [manifest.width as f32, manifest.height as f32],
                from_size: loaded[&from_index].size,
                to_size: loaded[&to_index].size,
                zoom_from: zoom_at(from),
                zoom_to: zoom_at(to),
                progress,
                transition: kind,
                padding: [0.0; 2],
            };
            let uniform = &uniforms[batch_slot];
            queue.write_buffer(uniform, 0, bytemuck::bytes_of(&params));
            let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: Some("active still pair and frame parameters"),
                layout: &layout,
                entries: &[
                    wgpu::BindGroupEntry {
                        binding: 0,
                        resource: wgpu::BindingResource::TextureView(&loaded[&from_index].view),
                    },
                    wgpu::BindGroupEntry {
                        binding: 1,
                        resource: wgpu::BindingResource::Sampler(&sampler),
                    },
                    wgpu::BindGroupEntry {
                        binding: 2,
                        resource: wgpu::BindingResource::TextureView(&loaded[&to_index].view),
                    },
                    wgpu::BindGroupEntry {
                        binding: 3,
                        resource: wgpu::BindingResource::Sampler(&sampler),
                    },
                    wgpu::BindGroupEntry {
                        binding: 4,
                        resource: uniform.as_entire_binding(),
                    },
                ],
            });
            {
                let mut pass = command_encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                    label: Some("compose transition frame"),
                    color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                        view: &output_views[batch_slot],
                        depth_slice: None,
                        resolve_target: None,
                        ops: wgpu::Operations {
                            load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                            store: wgpu::StoreOp::Store,
                        },
                    })],
                    depth_stencil_attachment: None,
                    timestamp_writes: None,
                    occlusion_query_set: None,
                    multiview_mask: None,
                });
                pass.set_pipeline(&pipeline);
                pass.set_bind_group(0, &bind_group, &[]);
                pass.draw(0..3, 0..1);
            }
            if direct_encoder.is_none() {
                command_encoder.copy_texture_to_buffer(
                    wgpu::TexelCopyTextureInfo {
                        texture: &outputs[batch_slot],
                        mip_level: 0,
                        origin: wgpu::Origin3d::ZERO,
                        aspect: wgpu::TextureAspect::All,
                    },
                    wgpu::TexelCopyBufferInfo {
                        buffer: &readbacks[batch_slot],
                        layout: wgpu::TexelCopyBufferLayout {
                            offset: 0,
                            bytes_per_row: Some(padded_row),
                            rows_per_image: Some(manifest.height),
                        },
                    },
                    wgpu::Extent3d {
                        width: manifest.width,
                        height: manifest.height,
                        depth_or_array_layers: 1,
                    },
                );
            }
        }
        queue.submit([command_encoder.finish()]);
        if let Some(encoder) = direct_encoder.as_mut() {
            let frames: Vec<(usize, u64)> = (0..batch_len)
                .map(|batch_slot| (batch_slot, batch_start + batch_slot as u64))
                .collect();
            let bitstreams = match encoder {
                #[cfg(windows)]
                DirectEncoder::Nvenc(nvenc) => nvenc.encode_batch(&frames)?,
                #[cfg(not(windows))]
                DirectEncoder::Disabled => {
                    return Err("Direct3D 12 NVENC is only available on Windows".into())
                }
            };
            for bitstream in bitstreams {
                stdout
                    .write_all(&bitstream)
                    .map_err(|e| format!("compressed H.264 output failed: {e}"))?;
            }
            stdout
                .flush()
                .map_err(|e| format!("compressed H.264 flush failed: {e}"))?;
        } else {
            write_readback_batch(
                device,
                &readbacks[..batch_len],
                padded_row,
                manifest.width,
                manifest.height,
                &mut stdout,
            )?;
        }
        let completed = batch_start + batch_len as u64;
        if completed % u64::from(manifest.fps.max(2) * 2) < BATCH_FRAMES as u64
            || completed == frame_count
        {
            eprintln!("RUST_RENDER_PROGRESS\t{}\t{}", completed, frame_count);
        }
    }
    if let Some(encoder) = direct_encoder.as_mut() {
        encoder.finish()?;
    }
    stdout
        .flush()
        .map_err(|e| format!("raw frame output failed: {e}"))?;
    Ok(())
}

fn image_at(images: &[ImageItem], time: f64) -> usize {
    images
        .partition_point(|image| image.start_sec <= time)
        .saturating_sub(1)
}

fn load_gpu_image(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    item: &ImageItem,
    max_texture_side: u32,
) -> Result<GpuImage, String> {
    let mut reader = image::ImageReader::open(&item.path)
        .map_err(|e| format!("could not open {}: {e}", item.path))?
        .with_guessed_format()
        .map_err(|e| format!("could not identify {}: {e}", item.path))?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(32_768);
    limits.max_image_height = Some(32_768);
    // Keep a corrupt or extremely large still from asking the decoder for
    // hundreds of megabytes before it can be resized to the output canvas.
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    let mut image = reader
        .decode()
        .map_err(|e| format!("could not decode {}: {e}", item.path))?
        .to_rgba8();
    let (width, height) = image.dimensions();
    if width == 0 || height == 0 {
        return Err(format!("image has no pixels: {}", item.path));
    }
    if width > max_texture_side || height > max_texture_side {
        let scale = (f64::from(max_texture_side) / f64::from(width))
            .min(f64::from(max_texture_side) / f64::from(height))
            .min(1.0);
        let target_width = (f64::from(width) * scale).round().max(1.0) as u32;
        let target_height = (f64::from(height) * scale).round().max(1.0) as u32;
        image = image::imageops::resize(&image, target_width, target_height, FilterType::Lanczos3);
    }
    let (width, height) = image.dimensions();
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("active still image"),
        size: wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: wgpu::TextureFormat::Rgba8Unorm,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    });
    queue.write_texture(
        wgpu::TexelCopyTextureInfo {
            texture: &texture,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        image.as_raw(),
        wgpu::TexelCopyBufferLayout {
            offset: 0,
            bytes_per_row: Some(width * 4),
            rows_per_image: Some(height),
        },
        wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        },
    );
    let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
    Ok(GpuImage {
        _texture: texture,
        view,
        size: [width as f32, height as f32],
    })
}

fn texture_entry(binding: u32) -> wgpu::BindGroupLayoutEntry {
    wgpu::BindGroupLayoutEntry {
        binding,
        visibility: wgpu::ShaderStages::FRAGMENT,
        ty: wgpu::BindingType::Texture {
            sample_type: wgpu::TextureSampleType::Float { filterable: true },
            view_dimension: wgpu::TextureViewDimension::D2,
            multisampled: false,
        },
        count: None,
    }
}

fn sampler_entry(binding: u32) -> wgpu::BindGroupLayoutEntry {
    wgpu::BindGroupLayoutEntry {
        binding,
        visibility: wgpu::ShaderStages::FRAGMENT,
        ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
        count: None,
    }
}

fn align_up(value: u32, alignment: u32) -> u32 {
    value.div_ceil(alignment) * alignment
}

fn write_readback_batch(
    device: &wgpu::Device,
    buffers: &[wgpu::Buffer],
    padded_row: u32,
    width: u32,
    height: u32,
    stdout: &mut impl Write,
) -> Result<(), String> {
    let (sender, receiver) = mpsc::channel();
    for (index, buffer) in buffers.iter().enumerate() {
        let slice = buffer.slice(..);
        let sender = sender.clone();
        slice.map_async(wgpu::MapMode::Read, move |result| {
            let _ = sender.send((index, result));
        });
    }
    drop(sender);
    device
        .poll(wgpu::PollType::Wait {
            submission_index: None,
            timeout: Some(Duration::from_secs(30)),
        })
        .map_err(|e| format!("GPU frame readback timed out: {e}"))?;
    let mut mapped_indices = Vec::with_capacity(buffers.len());
    for _ in 0..buffers.len() {
        let (index, result) = receiver
            .recv_timeout(Duration::from_secs(1))
            .map_err(|e| format!("GPU frame readback did not complete: {e}"))?;
        result.map_err(|e| format!("GPU frame buffer could not be mapped: {e}"))?;
        mapped_indices.push(index);
    }
    mapped_indices.sort_unstable();
    let row_bytes = width as usize * 4;
    for index in mapped_indices {
        let slice = buffers[index].slice(..);
        {
            let mapped = slice
                .get_mapped_range()
                .map_err(|e| format!("GPU frame buffer map failed: {e}"))?;
            for row in 0..height as usize {
                let start = row * padded_row as usize;
                stdout
                    .write_all(&mapped[start..start + row_bytes])
                    .map_err(|e| format!("raw frame output failed: {e}"))?;
            }
        }
        buffers[index].unmap();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_scheduler_uses_timestamp_boundaries() {
        let images = [
            ImageItem {
                path: "first.png".into(),
                start_sec: 0.0,
                end_sec: 4.0,
                duration: 4.0,
            },
            ImageItem {
                path: "second.png".into(),
                start_sec: 4.0,
                end_sec: 9.0,
                duration: 5.0,
            },
        ];
        assert_eq!(image_at(&images, 0.0), 0);
        assert_eq!(image_at(&images, 3.999), 0);
        assert_eq!(image_at(&images, 4.0), 1);
        assert_eq!(image_at(&images, 8.99), 1);
    }

    #[test]
    fn known_transitions_have_gpu_shader_ids() {
        assert_eq!(transition_id("fade"), 1);
        assert_eq!(transition_id("wipeleft"), 3);
        assert_eq!(transition_id("circleopen"), 5);
        assert_eq!(transition_id("hard_cut"), 0);
    }
}
