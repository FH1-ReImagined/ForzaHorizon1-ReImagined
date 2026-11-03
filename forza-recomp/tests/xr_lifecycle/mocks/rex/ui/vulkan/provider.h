#pragma once
#include <vulkan/vulkan.h>
namespace rex::ui {class GraphicsProvider{public:virtual ~GraphicsProvider()=default;};}
namespace rex::ui::vulkan {
struct InstanceFns{PFN_vkGetInstanceProcAddr vkGetInstanceProcAddr{};PFN_vkGetDeviceProcAddr vkGetDeviceProcAddr{};};
struct VulkanInstance{InstanceFns f; const InstanceFns& functions(){return f;} VkInstance instance(){return (VkInstance)1;}};
struct VulkanDevice{VulkanInstance i;struct Queue{VkQueue queue()const{return (VkQueue)1;}};Queue AcquireQueue(uint32_t,uint32_t){return{};} VkDevice device(){return (VkDevice)1;} uint32_t queue_family_graphics_compute(){return 0;} VulkanInstance* vulkan_instance(){return &i;} VkPhysicalDevice physical_device(){return (VkPhysicalDevice)1;} VkPhysicalDeviceProperties properties(){return {};}};
struct VulkanProvider:GraphicsProvider{VulkanDevice d;VulkanDevice* vulkan_device(){return &d;}};
}
