// Actual OpenXR bridge with SDK/API mocks: no device, runtime or guest code is loaded.
#include <cstdio>
#include <cstdlib>
#include <atomic>
#include "../../src/platform/xr.cpp"
namespace forza{void* SdkSymbol(const char*){return nullptr;}}
namespace forza::xr{bool TestPoseOffset(forza::vr::HeadPose&){return false;}bool StereoOffTest(){return false;}bool FrameWaitOffTest(){return false;}}
int32_t frame_rate=72;
using namespace forza::xr;
#define CHECK(x) do{if(!(x)){std::fprintf(stderr,"XR actual FAIL %d: %s\n",__LINE__,#x);return 1;}}while(false)
int submits=0,releases=0,destroys=0,waits=0,acquires=0,holds_released=0;
XrResult release_result=XR_SUCCESS; VkResult submit_result=VK_SUCCESS;
XrResult wait_result=XR_TIMEOUT_EXPIRED; VkResult fence_result=VK_TIMEOUT;
VKAPI_ATTR VkResult VKAPI_CALL WaitFence(VkDevice,uint32_t,const VkFence*,VkBool32,uint64_t){++waits;return fence_result;}
XRAPI_ATTR XrResult XRAPI_CALL Acquire(XrSwapchain,const XrSwapchainImageAcquireInfo*,uint32_t*i){++acquires;*i=0;return XR_SUCCESS;}
XRAPI_ATTR XrResult XRAPI_CALL WaitImage(XrSwapchain,const XrSwapchainImageWaitInfo*){++waits;return wait_result;}
XRAPI_ATTR XrResult XRAPI_CALL Release(XrSwapchain,const XrSwapchainImageReleaseInfo*){++releases;return release_result;}
VKAPI_ATTR VkResult VKAPI_CALL ResetPool(VkDevice,VkCommandPool,VkCommandPoolResetFlags){return VK_SUCCESS;}
VKAPI_ATTR VkResult VKAPI_CALL Begin(VkCommandBuffer,const VkCommandBufferBeginInfo*){return VK_SUCCESS;}
VKAPI_ATTR VkResult VKAPI_CALL End(VkCommandBuffer){return VK_SUCCESS;}
VKAPI_ATTR void VKAPI_CALL BarrierMock(VkCommandBuffer,VkPipelineStageFlags,VkPipelineStageFlags,VkDependencyFlags,uint32_t,const VkMemoryBarrier*,uint32_t,const VkBufferMemoryBarrier*,uint32_t,const VkImageMemoryBarrier*){}
VKAPI_ATTR void VKAPI_CALL Blit(VkCommandBuffer,VkImage,VkImageLayout,VkImage,VkImageLayout,uint32_t,const VkImageBlit*,VkFilter){}
VKAPI_ATTR void VKAPI_CALL Copy(VkCommandBuffer,VkImage,VkImageLayout,VkImage,VkImageLayout,uint32_t,const VkImageCopy*){}
VKAPI_ATTR VkResult VKAPI_CALL Submit(VkQueue,uint32_t,const VkSubmitInfo*,VkFence){++submits;return submit_result;}
XRAPI_ATTR XrResult XRAPI_CALL DestroyXR(XrSwapchain){++destroys;return XR_SUCCESS;}
VKAPI_ATTR void VKAPI_CALL DestroyImage(VkDevice,VkImage,const VkAllocationCallbacks*){++destroys;}
VKAPI_ATTR void VKAPI_CALL Free(VkDevice,VkDeviceMemory,const VkAllocationCallbacks*){++destroys;}
VKAPI_ATTR VkResult VKAPI_CALL ResetFence(VkDevice,uint32_t,const VkFence*){return VK_SUCCESS;}
void DropHold(uint32_t){++holds_released;}
int main(int argc,char**argv){
 rex::ui::vulkan::VulkanDevice device;g.device=&device;g.picture.swapchain=(XrSwapchain)1;g.picture.width=g.picture.height=16;g.picture.images={(VkImage)2};g.picture.staging=(VkImage)3;g.picture.staging_memory=(VkDeviceMemory)4;
 g.xr.AcquireSwapchainImage=Acquire;g.xr.WaitSwapchainImage=WaitImage;g.xr.ReleaseSwapchainImage=Release;g.xr.DestroySwapchain=DestroyXR;
 g.release_frame=DropHold;g.vk.ResetFences=ResetFence;g.vk.WaitForFences=WaitFence;g.vk.ResetCommandPool=ResetPool;g.vk.BeginCommandBuffer=Begin;g.vk.EndCommandBuffer=End;g.vk.CmdPipelineBarrier=BarrierMock;g.vk.CmdBlitImage=Blit;g.vk.CmdCopyImage=Copy;g.vk.QueueSubmit=Submit;g.vk.DestroyImage=DestroyImage;g.vk.FreeMemory=Free;
 if(argc==2&&std::string(argv[1])=="timeout"){
 RexGuestOutputFrame frame{};frame.width=frame.height=16;frame.image=(VkImage)5;frame.handle=1;
 bool copied=CopyFrame(frame,false);std::printf("timeout copied=%d submitted=%d released=%d\n",copied,submits,releases);CHECK(!copied&&submits==0&&releases==0);
 CHECK(g.picture.image_acquired&&acquires==1);wait_result=XR_SUCCESS;
 CHECK(CopyFrame(frame,false));CHECK(acquires==1&&submits==1&&releases==1&&!g.picture.image_acquired);
 CHECK(g.slots[0].busy&&g.slots[0].hold==1&&holds_released==0);
 fence_result=VK_SUCCESS;CHECK(FinishSlot(g.slots[0],0));CHECK(holds_released==1&&!g.slots[0].busy);
 }else if(argc==2&&std::string(argv[1])=="fence"){
 g.slots[0].busy=true;g.slots[0].hold=1;DestroySwapchain();std::printf("fence waits=%d destroyed=%d stillbusy=%d\n",waits,destroys,g.slots[0].busy);CHECK(destroys==0&&g.slots[0].busy&&holds_released==0);
 fence_result=VK_SUCCESS;CHECK(DestroySwapchain());CHECK(destroys==3&&holds_released==1&&!g.slots[0].busy);
 }else if(argc==2&&std::string(argv[1])=="loss"){
 RexGuestOutputFrame frame{};frame.width=frame.height=16;frame.image=(VkImage)5;frame.handle=1;
 wait_result=XR_ERROR_SESSION_LOST;CHECK(!CopyFrame(frame,false));CHECK(g.stop&&submits==0&&releases==0);
 }else if(argc==2&&std::string(argv[1])=="device-loss"){
 g.slots[0].busy=true;g.slots[0].hold=1;fence_result=VK_ERROR_DEVICE_LOST;
 CHECK(DestroySwapchain());CHECK(g.stop&&destroys==3&&holds_released==1);
 }else if(argc==2&&std::string(argv[1])=="release-failure"){
 RexGuestOutputFrame frame{};frame.width=frame.height=16;frame.image=(VkImage)5;frame.handle=1;
 wait_result=XR_SUCCESS;release_result=XR_ERROR_RUNTIME_FAILURE;
 CHECK(CopyFrame(frame,false));CHECK(g.stop&&g.slots[0].busy&&g.slots[0].hold==1&&holds_released==0&&!g.picture.have_image);
 fence_result=VK_SUCCESS;CHECK(FinishSlot(g.slots[0],0));CHECK(holds_released==1);
 }else if(argc==2&&std::string(argv[1])=="submit-failure"){
 RexGuestOutputFrame frame{};frame.width=frame.height=16;frame.image=(VkImage)5;frame.handle=1;
 wait_result=XR_SUCCESS;submit_result=VK_ERROR_DEVICE_LOST;
 CHECK(!CopyFrame(frame,false));CHECK(g.stop&&!g.slots[0].busy&&holds_released==0);
 }else if(argc==2&&std::string(argv[1])=="stop"){
 RexGuestOutputFrame frame{};frame.width=frame.height=16;g.stop=true;
 CHECK(!CopyFrame(frame,false));CHECK(acquires==0&&waits==0&&submits==0&&releases==0);
 }else if(argc==2&&std::string(argv[1])=="shutdown"){
 g.instance=XR_NULL_HANDLE;
 g.thread=std::thread([]{std::this_thread::sleep_for(std::chrono::milliseconds(3200));g.finished=true;});
 Shutdown();const bool complete=g.finished.load();while(!g.finished)std::this_thread::sleep_for(std::chrono::milliseconds(10));
 CHECK(complete&&!g.thread.joinable());
 }else return 2;
 return 0;
}
