#!/usr/bin/env python3
"""Validate the resolve downscale of 32-bit pixels (point, centre and the RGBA8 box filter) using
physical 16-byte resolve quads."""
from pathlib import Path
import sys, re, struct, json

import argparse
parser = argparse.ArgumentParser(description="Test the embedded resolve downscale SPIR-V against independently encoded scaled resolve quads. Requires Python vulkan; no game assets.")
parser.add_argument("--header", required=True, type=Path)
parser.add_argument("--original-control", action="store_true", help="Require at least one failure from the pre-fix shader")
args = parser.parse_args()
import vulkan as v

instance = v.vkCreateInstance(v.VkInstanceCreateInfo(pApplicationInfo=v.VkApplicationInfo(apiVersion=v.VK_MAKE_VERSION(1, 3, 0))), None)
physical = next(p for p in v.vkEnumeratePhysicalDevices(instance) if v.vkGetPhysicalDeviceProperties(p).deviceType != v.VK_PHYSICAL_DEVICE_TYPE_CPU)
family = next(i for i, p in enumerate(v.vkGetPhysicalDeviceQueueFamilyProperties(physical)) if p.queueFlags & v.VK_QUEUE_COMPUTE_BIT)
device = v.vkCreateDevice(physical, v.VkDeviceCreateInfo(pEnabledFeatures=v.vkGetPhysicalDeviceFeatures(physical), queueCreateInfoCount=1, pQueueCreateInfos=[v.VkDeviceQueueCreateInfo(queueFamilyIndex=family, queueCount=1, pQueuePriorities=[1.0])]), None)
queue = v.vkGetDeviceQueue(device, family, 0)
memory = v.vkGetPhysicalDeviceMemoryProperties(physical)
bindings = [v.VkDescriptorSetLayoutBinding(binding=i, descriptorType=v.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, descriptorCount=1, stageFlags=v.VK_SHADER_STAGE_COMPUTE_BIT) for i in range(2)]
layout = v.vkCreateDescriptorSetLayout(device, v.VkDescriptorSetLayoutCreateInfo(bindingCount=2, pBindings=bindings), None)
pipeline_layout = v.vkCreatePipelineLayout(device, v.VkPipelineLayoutCreateInfo(setLayoutCount=1, pSetLayouts=[layout], pushConstantRangeCount=1, pPushConstantRanges=[v.VkPushConstantRange(stageFlags=v.VK_SHADER_STAGE_COMPUTE_BIT, offset=0, size=24)]), None)
pool = v.vkCreateDescriptorPool(device, v.VkDescriptorPoolCreateInfo(flags=v.VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT, maxSets=1, poolSizeCount=1, pPoolSizes=[v.VkDescriptorPoolSize(type=v.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, descriptorCount=2)]), None)
command_pool = v.vkCreateCommandPool(device, v.VkCommandPoolCreateInfo(queueFamilyIndex=family, flags=v.VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT), None)
command = v.vkAllocateCommandBuffers(device, v.VkCommandBufferAllocateInfo(commandPool=command_pool, level=v.VK_COMMAND_BUFFER_LEVEL_PRIMARY, commandBufferCount=1))[0]

def buffer(data):
    handle = v.vkCreateBuffer(device, v.VkBufferCreateInfo(size=len(data), usage=v.VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, sharingMode=v.VK_SHARING_MODE_EXCLUSIVE), None)
    req = v.vkGetBufferMemoryRequirements(device, handle)
    flags = v.VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | v.VK_MEMORY_PROPERTY_HOST_COHERENT_BIT
    index = next(i for i in range(memory.memoryTypeCount) if req.memoryTypeBits & (1 << i) and memory.memoryTypes[i].propertyFlags & flags == flags)
    allocation = v.vkAllocateMemory(device, v.VkMemoryAllocateInfo(allocationSize=req.size, memoryTypeIndex=index), None)
    v.vkBindBufferMemory(device, handle, allocation, 0)
    mapped = v.vkMapMemory(device, allocation, 0, len(data), 0)
    mapped[:] = data
    return handle, allocation, mapped

reports = {}
for label in ['shader']:
    header = args.header.read_text()
    words = [int(x, 16) for x in re.findall(r'0x[0-9a-fA-F]+', header[header.index('{', header.index('const uint32_t')):])]
    code = struct.pack('<' + 'I' * len(words), *words)
    module = v.vkCreateShaderModule(device, v.VkShaderModuleCreateInfo(codeSize=len(code), pCode=code), None)
    # Kept in a variable: a temporary stage structure can be freed before the call reads it.
    stage = v.VkPipelineShaderStageCreateInfo(stage=v.VK_SHADER_STAGE_COMPUTE_BIT, module=module, pName='main')
    pipeline = v.vkCreateComputePipelines(device, v.VK_NULL_HANDLE, 1, [v.VkComputePipelineCreateInfo(stage=stage, layout=pipeline_layout)], None)[0]
    passed = failed = filtered_failed = 0
    for sx in [1, 2, 3]:
        for sy in [1, 2, 3]:
            for offset in [0, 16, 256]:
                tiles = 1 if offset == 0 else 2
                samples = sx * sy
                values = [((i * 17 + 3) & 255) | (((i * 29 + 7) & 255) << 8) | (((i * 43 + 11) & 255) << 16) | (((i * 61 + 19) & 255) << 24) for i in range(tiles * 1024 * samples)]
                # Encode the renderer's physical quad layout from a logical
                # raster of four adjacent pixels and independently varied samples.
                packed = []
                for quad in range(tiles * 256):
                    for xgroup in range(sx):
                        for y in range(sy):
                            for component in range(4):
                                x = xgroup * 4 + component
                                pixel = quad * 4 + x // sx
                                sample = y * sx + x % sx
                                packed.append(values[pixel * samples + sample])
                source_data = b'\xee' * offset + struct.pack('<' + 'I' * len(packed), *packed)
                # Modes: 0 the top-left sample, 1 the centre one (sample (sx/2, sy/2)), 2 the
                # RGBA8 box filter (the average of all samples).
                for mode in [0, 1, 2]:
                    expected = []
                    for pixel in range(tiles * 1024):
                        block = values[pixel * samples:(pixel + 1) * samples]
                        if mode == 2:
                            word = sum(((sum((w >> shift) & 255 for w in block) + samples // 2) // samples) << shift for shift in [0, 8, 16, 24])
                        else:
                            word = block[(sy // 2) * sx + sx // 2 if mode == 1 and samples > 1 else 0]
                        expected.append(word)
                    expected_data = struct.pack('<' + 'I' * len(expected), *expected) + b'\xcd' * 64
                    src = buffer(source_data)
                    dst = buffer(b'\xcd' * len(expected_data))
                    descriptors = v.vkAllocateDescriptorSets(device, v.VkDescriptorSetAllocateInfo(descriptorPool=pool, descriptorSetCount=1, pSetLayouts=[layout]))
                    infos = [v.VkDescriptorBufferInfo(buffer=b[0], offset=0, range=len(data)) for b, data in [(src, source_data), (dst, expected_data)]]
                    writes = [v.VkWriteDescriptorSet(dstSet=descriptors[0], dstBinding=i, descriptorCount=1, descriptorType=v.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, pBufferInfo=[infos[i]]) for i in range(2)]
                    v.vkUpdateDescriptorSets(device, 2, writes, 0, None)
                    v.vkResetCommandBuffer(command, 0)
                    v.vkBeginCommandBuffer(command, v.VkCommandBufferBeginInfo())
                    v.vkCmdBindPipeline(command, v.VK_PIPELINE_BIND_POINT_COMPUTE, pipeline)
                    v.vkCmdBindDescriptorSets(command, v.VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, descriptors, 0, None)
                    push = v.ffi.new('uint32_t[]', [sx, sy, 2, tiles, offset, mode])
                    v.vkCmdPushConstants(command, pipeline_layout, v.VK_SHADER_STAGE_COMPUTE_BIT, 0, 24, push)
                    v.vkCmdDispatch(command, tiles + 1, 1, 1)
                    barrier = v.VkMemoryBarrier(srcAccessMask=v.VK_ACCESS_SHADER_WRITE_BIT, dstAccessMask=v.VK_ACCESS_HOST_READ_BIT)
                    v.vkCmdPipelineBarrier(command, v.VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, v.VK_PIPELINE_STAGE_HOST_BIT, 0, 1, [barrier], 0, None, 0, None)
                    v.vkEndCommandBuffer(command)
                    v.vkQueueSubmit(queue, 1, [v.VkSubmitInfo(commandBufferCount=1, pCommandBuffers=[command])], v.VK_NULL_HANDLE)
                    v.vkQueueWaitIdle(queue)
                    correct = bytes(dst[2]) == expected_data
                    passed += correct
                    failed += not correct
                    filtered_failed += not correct and mode == 2
                    v.vkFreeDescriptorSets(device, pool, 1, descriptors)
                    for handle, allocation, _ in [src, dst]:
                        v.vkUnmapMemory(device, allocation)
                        v.vkDestroyBuffer(device, handle, None)
                        v.vkFreeMemory(device, allocation, None)
    reports[label] = {'passed': passed, 'failed': failed, 'filtered_failed': filtered_failed}
    print(label, reports[label], flush=True)
    v.vkDestroyPipeline(device, pipeline, None)
    v.vkDestroyShaderModule(device, module, None)
failed = reports['shader']['failed']
v.vkDeviceWaitIdle(device)
v.vkDestroyCommandPool(device, command_pool, None)
v.vkDestroyDescriptorPool(device, pool, None)
v.vkDestroyPipelineLayout(device, pipeline_layout, None)
v.vkDestroyDescriptorSetLayout(device, layout, None)
v.vkDestroyDevice(device, None)
v.vkDestroyInstance(instance, None)

assert failed > 0 if args.original_control else failed == 0
