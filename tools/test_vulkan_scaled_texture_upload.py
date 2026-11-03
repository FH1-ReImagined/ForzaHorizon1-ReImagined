#!/usr/bin/env python3
"""Compare direct-image uploads with the original GPU loader, bit for bit.

No game files or NVIDIA libraries are needed. Requires a Vulkan-capable NVIDIA
device, the optional Python `vulkan` package, and an SDK with this patch applied.
Tests independent X/Y scales 1..7, all four endian modes, aligned/ragged extents,
and an offset source binding. Depth cases compare exact output bits with the
original compiled UNorm24/float20e4 loaders, including conversion boundaries.
"""

from pathlib import Path
import argparse
import array
import re
import struct
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--sdk", required=True, type=Path, help="SDK with the applied patch series"
)
parser.add_argument(
    "--format", choices=["all", "rgba", "rgb10a2", "r32f", "depth-unorm", "depth-float"], default="all"
)
args = parser.parse_args()
if args.format == "all":
    for fmt in ["rgba", "rgb10a2", "r32f", "depth-unorm", "depth-float"]:
        subprocess.run(
            [sys.executable, __file__, "--sdk", str(args.sdk), "--format", fmt],
            check=True,
        )
    sys.exit(0)
try:
    import vulkan as v
except ImportError:
    raise SystemExit(
        "Install the optional test dependency: python -m pip install vulkan"
    )


def shader_bytes(name):
    text = (args.sdk / "src/graphics/shaders/vulkan_spirv" / name).read_text()
    body = text[text.index("{") + 1 : text.index("}")]
    return struct.pack(
        "<" + "I" * len(re.findall(r"0x[0-9a-fA-F]+", body)),
        *[int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", body)]
    )


inst = v.vkCreateInstance(
    v.VkInstanceCreateInfo(
        pApplicationInfo=v.VkApplicationInfo(apiVersion=v.VK_MAKE_VERSION(1, 3, 0))
    ),
    None,
)
phy = next(
    p
    for p in v.vkEnumeratePhysicalDevices(inst)
    if v.vkGetPhysicalDeviceProperties(p).vendorID == 0x10DE
)
family = next(
    i
    for i, p in enumerate(v.vkGetPhysicalDeviceQueueFamilyProperties(phy))
    if p.queueFlags & v.VK_QUEUE_COMPUTE_BIT
)
dev = v.vkCreateDevice(
    phy,
    v.VkDeviceCreateInfo(
        pEnabledFeatures=v.vkGetPhysicalDeviceFeatures(phy),
        queueCreateInfoCount=1,
        pQueueCreateInfos=[
            v.VkDeviceQueueCreateInfo(
                queueFamilyIndex=family, queueCount=1, pQueuePriorities=[1.0]
            )
        ],
    ),
    None,
)
q = v.vkGetDeviceQueue(dev, family, 0)
mem = v.vkGetPhysicalDeviceMemoryProperties(phy)
flags = v.VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | v.VK_MEMORY_PROPERTY_HOST_COHERENT_BIT
print(
    "Memory types:",
    [(i, mem.memoryTypes[i].propertyFlags) for i in range(mem.memoryTypeCount)],
    flush=True,
)


def buffer(data):
    b = v.vkCreateBuffer(
        dev,
        v.VkBufferCreateInfo(
            size=len(data),
            usage=v.VK_BUFFER_USAGE_STORAGE_BUFFER_BIT
            | v.VK_BUFFER_USAGE_TRANSFER_SRC_BIT
            | v.VK_BUFFER_USAGE_TRANSFER_DST_BIT,
            sharingMode=v.VK_SHARING_MODE_EXCLUSIVE,
        ),
        None,
    )
    req = v.vkGetBufferMemoryRequirements(dev, b)
    ids = [
        i
        for i in range(mem.memoryTypeCount)
        if req.memoryTypeBits & (1 << i)
        and mem.memoryTypes[i].propertyFlags & flags == flags
    ]
    ids.sort(
        key=lambda i: not bool(
            mem.memoryTypes[i].propertyFlags & v.VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT
        )
    )
    a = v.vkAllocateMemory(
        dev,
        v.VkMemoryAllocateInfo(allocationSize=req.size, memoryTypeIndex=ids[0]),
        None,
    )
    v.vkBindBufferMemory(dev, b, a, 0)
    mapped = v.vkMapMemory(dev, a, 0, len(data), 0)
    mapped[:] = data
    return b, a, mapped


depth_mode = {"depth-unorm": 1, "depth-float": 2}.get(args.format, 0)
# Independent test inputs, including zero with stencil set, denormal/normal
# boundaries, UNorm rounding boundaries and the common 0xC00000 clear value.
depth_words = [
    (depth << 8) | stencil
    for depth in (0, 1, 2, 0x7FFFF, 0xFFFFF, 0x100000, 0x100001,
                  0x7FFFFF, 0x800000, 0x800001, 0xBFFFFF, 0xC00000,
                  0xC00001, 0xEFFFFF, 0xF00000, 0xFFFFFF)
    for stencil in (0, 255)
]
boundary_words = []
for word in depth_words:
    halves = ((word & 0x00FF00FF) << 8) | ((word & 0xFF00FF00) >> 8)
    boundary_words += [word, halves, ((word << 16) | (word >> 16)) & 0xFFFFFFFF,
                       ((halves << 16) | (halves >> 16)) & 0xFFFFFFFF]
src = buffer(
    array.array(
        "I",
        (
            boundary_words[i % 256] if depth_mode and i % 256 < len(boundary_words)
            else (i * 0x9E3779B1 ^ ((i >> 5) * 0x85EBCA6B)) & 0xFFFFFFFF
            for i in range(8 * 1024 * 1024)
        ),
    ).tobytes()
)
dst = buffer(b"\xcd" * (16 * 1024 * 1024))
layout = v.vkCreateDescriptorSetLayout(
    dev,
    v.VkDescriptorSetLayoutCreateInfo(
        bindingCount=1,
        pBindings=[
            v.VkDescriptorSetLayoutBinding(
                binding=0,
                descriptorType=v.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                descriptorCount=1,
                stageFlags=v.VK_SHADER_STAGE_COMPUTE_BIT,
            )
        ],
    ),
    None,
)
pl = v.vkCreatePipelineLayout(
    dev,
    v.VkPipelineLayoutCreateInfo(
        setLayoutCount=2,
        pSetLayouts=[layout, layout],
        pushConstantRangeCount=1,
        pPushConstantRanges=[
            v.VkPushConstantRange(
                stageFlags=v.VK_SHADER_STAGE_COMPUTE_BIT, offset=0, size=40
            )
        ],
    ),
    None,
)
pool = v.vkCreateDescriptorPool(
    dev,
    v.VkDescriptorPoolCreateInfo(
        maxSets=2,
        poolSizeCount=1,
        pPoolSizes=[
            v.VkDescriptorPoolSize(
                type=v.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, descriptorCount=2
            )
        ],
    ),
    None,
)
sets = v.vkAllocateDescriptorSets(
    dev,
    v.VkDescriptorSetAllocateInfo(
        descriptorPool=pool, descriptorSetCount=2, pSetLayouts=[layout, layout]
    ),
)
v.vkUpdateDescriptorSets(
    dev,
    2,
    [
        v.VkWriteDescriptorSet(
            dstSet=sets[i],
            dstBinding=0,
            descriptorCount=1,
            descriptorType=v.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            pBufferInfo=[
                v.VkDescriptorBufferInfo(buffer=b[0], offset=0, range=len(b[2]))
            ],
        )
        for i, b in enumerate([dst, src])
    ],
    0,
    None,
)
cp = v.vkCreateCommandPool(
    dev,
    v.VkCommandPoolCreateInfo(
        queueFamilyIndex=family, flags=v.VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT
    ),
    None,
)
cmd = v.vkAllocateCommandBuffers(
    dev,
    v.VkCommandBufferAllocateInfo(
        commandPool=cp, level=v.VK_COMMAND_BUFFER_LEVEL_PRIMARY, commandBufferCount=1
    ),
)[0]
# Keep the nested stage alive until vkCreateComputePipelines returns (CFFI).
reference_shader = {
    "depth-unorm": "texture_load_depth_unorm_scaled_cs.h",
    "depth-float": "texture_load_depth_float_scaled_cs.h",
}.get(args.format, "texture_load_32bpb_scaled_cs.h")
code = shader_bytes(reference_shader)
module = v.vkCreateShaderModule(
    dev, v.VkShaderModuleCreateInfo(codeSize=len(code), pCode=code), None
)
stage = v.VkPipelineShaderStageCreateInfo(
    stage=v.VK_SHADER_STAGE_COMPUTE_BIT, module=module, pName="main"
)
old = v.vkCreateComputePipelines(
    dev,
    v.VK_NULL_HANDLE,
    1,
    [v.VkComputePipelineCreateInfo(stage=stage, layout=pl, basePipelineIndex=-1)],
    None,
)[0]
modules = {"original": module}
pipelines = {"original": old}


def run(p, push, groups):
    v.vkResetCommandBuffer(cmd, 0)
    v.vkBeginCommandBuffer(cmd, v.VkCommandBufferBeginInfo())
    v.vkCmdBindPipeline(cmd, v.VK_PIPELINE_BIND_POINT_COMPUTE, p)
    v.vkCmdBindDescriptorSets(
        cmd, v.VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, 2, sets, 0, None
    )
    pc = v.ffi.new("uint32_t[]", push)
    v.vkCmdPushConstants(cmd, pl, v.VK_SHADER_STAGE_COMPUTE_BIT, 0, 40, pc)
    v.vkCmdDispatch(cmd, *groups)
    barrier = v.VkMemoryBarrier(
        srcAccessMask=v.VK_ACCESS_SHADER_WRITE_BIT,
        dstAccessMask=v.VK_ACCESS_HOST_READ_BIT,
    )
    v.vkCmdPipelineBarrier(
        cmd,
        v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
        v.VK_PIPELINE_STAGE_HOST_BIT,
        0,
        1,
        [barrier],
        0,
        None,
        0,
        None,
    )
    v.vkEndCommandBuffer(cmd)
    v.vkQueueSubmit(
        q,
        1,
        [v.VkSubmitInfo(commandBufferCount=1, pCommandBuffers=[cmd])],
        v.VK_NULL_HANDLE,
    )
    v.vkQueueWaitIdle(q)


format_name = args.format
image_format = {
    "r32ui": v.VK_FORMAT_R32_UINT,
    "rgba": v.VK_FORMAT_R8G8B8A8_UNORM,
    "rgb10a2": v.VK_FORMAT_A2B10G10R10_UNORM_PACK32,
    "r32f": v.VK_FORMAT_R32_SFLOAT,
    "depth-unorm": v.VK_FORMAT_R32_SFLOAT,
    "depth-float": v.VK_FORMAT_R32_SFLOAT,
}[format_name]
print("Image format:", format_name, flush=True)
image = v.vkCreateImage(
    dev,
    v.VkImageCreateInfo(
        flags=v.VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT,
        imageType=v.VK_IMAGE_TYPE_2D,
        format=image_format,
        extent=v.VkExtent3D(width=512, height=256, depth=1),
        mipLevels=1,
        arrayLayers=1,
        samples=v.VK_SAMPLE_COUNT_1_BIT,
        tiling=v.VK_IMAGE_TILING_OPTIMAL,
        usage=v.VK_IMAGE_USAGE_SAMPLED_BIT
        | v.VK_IMAGE_USAGE_STORAGE_BIT
        | v.VK_IMAGE_USAGE_TRANSFER_SRC_BIT
        | v.VK_IMAGE_USAGE_TRANSFER_DST_BIT,
        sharingMode=v.VK_SHARING_MODE_EXCLUSIVE,
        initialLayout=v.VK_IMAGE_LAYOUT_UNDEFINED,
    ),
    None,
)
req = v.vkGetImageMemoryRequirements(dev, image)
mi = next(
    i
    for i in range(mem.memoryTypeCount)
    if req.memoryTypeBits & (1 << i)
    and mem.memoryTypes[i].propertyFlags & v.VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT
)
image_mem = v.vkAllocateMemory(
    dev, v.VkMemoryAllocateInfo(allocationSize=req.size, memoryTypeIndex=mi), None
)
v.vkBindImageMemory(dev, image, image_mem, 0)
sub = v.VkImageSubresourceRange(
    aspectMask=v.VK_IMAGE_ASPECT_COLOR_BIT,
    baseMipLevel=0,
    levelCount=1,
    baseArrayLayer=0,
    layerCount=1,
)
view = v.vkCreateImageView(
    dev,
    v.VkImageViewCreateInfo(
        image=image,
        viewType=v.VK_IMAGE_VIEW_TYPE_2D,
        format=v.VK_FORMAT_R32_UINT,
        subresourceRange=sub,
    ),
    None,
)
il = v.vkCreateDescriptorSetLayout(
    dev,
    v.VkDescriptorSetLayoutCreateInfo(
        bindingCount=1,
        pBindings=[
            v.VkDescriptorSetLayoutBinding(
                binding=0,
                descriptorType=v.VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                descriptorCount=1,
                stageFlags=v.VK_SHADER_STAGE_COMPUTE_BIT,
            )
        ],
    ),
    None,
)
ipl = v.vkCreatePipelineLayout(
    dev,
    v.VkPipelineLayoutCreateInfo(
        setLayoutCount=2,
        pSetLayouts=[il, layout],
        pushConstantRangeCount=1,
        pPushConstantRanges=[
            v.VkPushConstantRange(
                stageFlags=v.VK_SHADER_STAGE_COMPUTE_BIT, offset=0, size=40
            )
        ],
    ),
    None,
)
ipool = v.vkCreateDescriptorPool(
    dev,
    v.VkDescriptorPoolCreateInfo(
        maxSets=1,
        poolSizeCount=1,
        pPoolSizes=[
            v.VkDescriptorPoolSize(
                type=v.VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, descriptorCount=1
            )
        ],
    ),
    None,
)
iset = v.vkAllocateDescriptorSets(
    dev,
    v.VkDescriptorSetAllocateInfo(
        descriptorPool=ipool, descriptorSetCount=1, pSetLayouts=[il]
    ),
)[0]
v.vkUpdateDescriptorSets(
    dev,
    1,
    [
        v.VkWriteDescriptorSet(
            dstSet=iset,
            dstBinding=0,
            descriptorType=v.VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
            descriptorCount=1,
            pImageInfo=[
                v.VkDescriptorImageInfo(
                    imageView=view, imageLayout=v.VK_IMAGE_LAYOUT_GENERAL
                )
            ],
        )
    ],
    0,
    None,
)
code = shader_bytes("texture_load_32bpb_scaled_image_cs.h")
imodule = v.vkCreateShaderModule(
    dev, v.VkShaderModuleCreateInfo(codeSize=len(code), pCode=code), None
)
stage = v.VkPipelineShaderStageCreateInfo(
    stage=v.VK_SHADER_STAGE_COMPUTE_BIT, module=imodule, pName="main"
)
ipipeline = v.vkCreateComputePipelines(
    dev,
    v.VK_NULL_HANDLE,
    1,
    [v.VkComputePipelineCreateInfo(stage=stage, layout=ipl, basePipelineIndex=-1)],
    None,
)[0]
canary = b"\xcd" * (512 * 256 * 4)
readback = buffer(canary)


def begin():
    v.vkResetCommandBuffer(cmd, 0)
    v.vkBeginCommandBuffer(cmd, v.VkCommandBufferBeginInfo())


def finish():
    v.vkEndCommandBuffer(cmd)
    v.vkQueueSubmit(
        q,
        1,
        [v.VkSubmitInfo(commandBufferCount=1, pCommandBuffers=[cmd])],
        v.VK_NULL_HANDLE,
    )
    v.vkQueueWaitIdle(q)


def image_barrier(old, new, sa, da, ss, ds):
    b = v.VkImageMemoryBarrier(
        srcAccessMask=sa,
        dstAccessMask=da,
        oldLayout=old,
        newLayout=new,
        srcQueueFamilyIndex=v.VK_QUEUE_FAMILY_IGNORED,
        dstQueueFamilyIndex=v.VK_QUEUE_FAMILY_IGNORED,
        image=image,
        subresourceRange=sub,
    )
    v.vkCmdPipelineBarrier(cmd, ss, ds, 0, 0, None, 0, None, 1, [b])


def bind_image(push):
    v.vkCmdBindPipeline(cmd, v.VK_PIPELINE_BIND_POINT_COMPUTE, ipipeline)
    v.vkCmdBindDescriptorSets(
        cmd, v.VK_PIPELINE_BIND_POINT_COMPUTE, ipl, 0, 2, [iset, sets[1]], 0, None
    )
    image_push = [push[0] | (depth_mode << 10), *push[1:]]
    pc = v.ffi.new("uint32_t[]", image_push)
    v.vkCmdPushConstants(cmd, ipl, v.VK_SHADER_STAGE_COMPUTE_BIT, 0, 40, pc)


def copy_region(w, h, pitch=0, offset=0):
    return v.VkBufferImageCopy(
        bufferOffset=offset,
        bufferRowLength=pitch,
        bufferImageHeight=0,
        imageSubresource=v.VkImageSubresourceLayers(
            aspectMask=v.VK_IMAGE_ASPECT_COLOR_BIT,
            mipLevel=0,
            baseArrayLayer=0,
            layerCount=1,
        ),
        imageOffset=v.VkOffset3D(x=0, y=0, z=0),
        imageExtent=v.VkExtent3D(width=w, height=h, depth=1),
    )


begin()
image_barrier(
    v.VK_IMAGE_LAYOUT_UNDEFINED,
    v.VK_IMAGE_LAYOUT_GENERAL,
    0,
    v.VK_ACCESS_SHADER_WRITE_BIT,
    v.VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
    v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
)
finish()
passed = 0
for sx in range(1, 8):
    for sy in range(1, 8):
        for endian in range(4):
            for w, h in [(64, 32), (37, 19)]:
                width = w * sx
                height = h * sy
                pitch = ((width + 31) // 32) * 32 * 4
                offset = 256
                push = [
                    1 | (endian << 2) | (sx << 4) | (sy << 7),
                    128,
                    128,
                    32,
                    width,
                    height,
                    1,
                    offset,
                    pitch,
                    h,
                ]
                run(old, push, ((width + 31) // 32, (height + 31) // 32, 1))
                expected = b"".join(
                    bytes(dst[2][offset + y * pitch : offset + y * pitch + width * 4])
                    + b"\xcd" * ((512 - width) * 4)
                    for y in range(height)
                ) + b"\xcd" * (512 * (256 - height) * 4)
                # Check the unwritten image area as well as the output pixels.
                readback[2][:] = canary
                begin()
                image_barrier(
                    v.VK_IMAGE_LAYOUT_GENERAL,
                    v.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                    v.VK_ACCESS_SHADER_WRITE_BIT,
                    v.VK_ACCESS_TRANSFER_WRITE_BIT,
                    v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                    v.VK_PIPELINE_STAGE_TRANSFER_BIT,
                )
                v.vkCmdCopyBufferToImage(
                    cmd,
                    readback[0],
                    image,
                    v.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                    1,
                    [copy_region(512, 256)],
                )
                image_barrier(
                    v.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                    v.VK_IMAGE_LAYOUT_GENERAL,
                    v.VK_ACCESS_TRANSFER_WRITE_BIT,
                    v.VK_ACCESS_SHADER_WRITE_BIT,
                    v.VK_PIPELINE_STAGE_TRANSFER_BIT,
                    v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                )
                bind_image(push)
                v.vkCmdDispatch(cmd, (width + 127) // 128, (height + 7) // 8, 1)
                image_barrier(
                    v.VK_IMAGE_LAYOUT_GENERAL,
                    v.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                    v.VK_ACCESS_SHADER_WRITE_BIT,
                    v.VK_ACCESS_TRANSFER_READ_BIT,
                    v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                    v.VK_PIPELINE_STAGE_TRANSFER_BIT,
                )
                v.vkCmdCopyImageToBuffer(
                    cmd,
                    image,
                    v.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                    readback[0],
                    1,
                    [copy_region(512, 256)],
                )
                image_barrier(
                    v.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                    v.VK_IMAGE_LAYOUT_GENERAL,
                    v.VK_ACCESS_TRANSFER_READ_BIT,
                    v.VK_ACCESS_SHADER_WRITE_BIT,
                    v.VK_PIPELINE_STAGE_TRANSFER_BIT,
                    v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                )
                barrier = v.VkMemoryBarrier(
                    srcAccessMask=v.VK_ACCESS_TRANSFER_WRITE_BIT,
                    dstAccessMask=v.VK_ACCESS_HOST_READ_BIT,
                )
                v.vkCmdPipelineBarrier(
                    cmd,
                    v.VK_PIPELINE_STAGE_TRANSFER_BIT,
                    v.VK_PIPELINE_STAGE_HOST_BIT,
                    0,
                    1,
                    [barrier],
                    0,
                    None,
                    0,
                    None,
                )
                finish()
                actual = bytes(readback[2][: len(expected)])
                if expected != actual:
                    index = next(
                        i for i, (a, b) in enumerate(zip(expected, actual)) if a != b
                    )
                    raise AssertionError(
                        (
                            sx,
                            sy,
                            endian,
                            w,
                            h,
                            index,
                            expected[index : index + 16].hex(),
                            actual[index : index + 16].hex(),
                        )
                    )
                passed += 1
    print("PASS x scale", sx, passed, flush=True)
print("ALL", passed, "IMAGE UPLOAD CASES PASS", flush=True)
v.vkDeviceWaitIdle(dev)
v.vkDestroyPipeline(dev, ipipeline, None)
v.vkDestroyShaderModule(dev, imodule, None)
v.vkDestroyDescriptorPool(dev, ipool, None)
v.vkDestroyPipelineLayout(dev, ipl, None)
v.vkDestroyDescriptorSetLayout(dev, il, None)
v.vkDestroyImageView(dev, view, None)
v.vkDestroyImage(dev, image, None)
v.vkFreeMemory(dev, image_mem, None)
for p in pipelines.values():
    v.vkDestroyPipeline(dev, p, None)
for m in modules.values():
    v.vkDestroyShaderModule(dev, m, None)
v.vkDestroyCommandPool(dev, cp, None)
v.vkDestroyDescriptorPool(dev, pool, None)
v.vkDestroyPipelineLayout(dev, pl, None)
v.vkDestroyDescriptorSetLayout(dev, layout, None)
for b, a, m in [src, dst, readback]:
    v.vkUnmapMemory(dev, a)
    v.vkDestroyBuffer(dev, b, None)
    v.vkFreeMemory(dev, a, None)
v.vkDestroyDevice(dev, None)
v.vkDestroyInstance(inst, None)
