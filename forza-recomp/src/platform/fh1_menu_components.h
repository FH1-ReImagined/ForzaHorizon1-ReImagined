// Repository-owned navigation and composition shared by native FH1 menus.
// No tuning fields, guest addresses, serialized scenes or input polling here.
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>
#include <vector>

namespace forza::fh1_menu_components {
inline double SliderGridStart(double minimum,double zero,double quantum) {
  return zero+std::ceil((minimum-zero)/quantum)*quantum;
}
// Index zero denotes stock, rather than a numeric endpoint. Skip values that
// would format identically (including a saved value between generated steps).
inline std::optional<size_t> SliderNeighbor(std::span<const float> choices,
    size_t selected,float stock,float tolerance,int direction) {
  if(choices.size()<2||selected>=choices.size()||!std::isfinite(stock)||
      !std::isfinite(tolerance)||tolerance<0||!direction)return {};
  const float current=selected?choices[selected]:stock;
  auto first=choices.begin()+1,last=choices.end();
  if(direction>0){auto next=std::upper_bound(first,last,current+tolerance);
    return next==last?selected:size_t(next-choices.begin());}
  auto next=std::lower_bound(first,last,current-tolerance);
  return next==first?selected:size_t((next-1)-choices.begin());
}
struct Rect { float x=0,y=0,w=0,h=0; };
// tan(7 degrees). UI papers share this angle; media keeps its own aspect.
inline constexpr float kMenuShear = .1227845609f;
inline constexpr float kMenuScaleX = (1152.f - kMenuShear * (570.f - 32.f)) / 1152.f;
inline constexpr float kMenuTranslateX = 576.f * (kMenuScaleX - 1.f) + 210.f * kMenuShear;
inline Rect MenuQuad(Rect source) {
  const float width=kMenuScaleX*source.w;
  const float center=640+kMenuTranslateX+kMenuScaleX*(source.x+source.w/2-640)+
      kMenuShear*(360-source.y-source.h/2);
  return {center-width/2,source.y,width,source.h};
}
// Sample an upright image through a sheared quad. Texture V points upward,
// so its U coordinate follows the quad's horizontal displacement about V=.5.
inline std::array<float,16> UprightShearUV(std::array<float,16> matrix,float shear) {
  for(unsigned row=0;row<4;++row){
    matrix[4+row]+=shear*matrix[row];
    matrix[12+row]-=.5f*shear*matrix[row];
  }
  return matrix;
}
inline Rect BackdropBounds(uint32_t width,uint32_t height) {
  if(!width||!height){width=1280;height=720;}
  const float scale=std::min(float(width)/1280,float(height)/720);
  const float w=float(width)/scale*1.08f,h=float(height)/scale*1.12f;
  return {640-w/2,360-h/2,w,h};
}
struct Tab {
  std::string id, label;
  bool locked=false;
  std::string unavailable_label="LOCKED";
};
struct TabCell { size_t index=0; Rect bounds; bool selected=false,focused=false; };
class TabBar {
  std::vector<Tab> tabs_;
  std::string selected_, focused_;
  double motion_seconds_=.22;
  Rect motion_from_{};
  mutable Rect visual_{};
  mutable bool visual_valid_=false;
  mutable size_t first_visible_=0, visible_count_=0;
  size_t Index(std::string_view id) const {
    auto found=std::find_if(tabs_.begin(),tabs_.end(),[&](const Tab& tab){return tab.id==id;});
    return size_t(found-tabs_.begin());
  }
public:
  static constexpr double kSelectionSeconds=.22;
  void Advance(double seconds) {
    if(!std::isfinite(seconds)||seconds<0)return;
    motion_seconds_=std::min(kSelectionSeconds,motion_seconds_+seconds);
  }
  // Stable IDs preserve selection across capability/label updates. Locked tabs
  // remain selectable so their explanation is reachable with any input device.
  bool SetTabs(std::vector<Tab> tabs) {
    if(tabs.empty() || tabs.size()>64)return false;
    std::unordered_set<std::string> ids;
    for(const auto& tab:tabs)if(tab.id.empty()||tab.label.empty()||!ids.insert(tab.id).second)return false;
    tabs_=std::move(tabs);
    first_visible_=std::min(first_visible_,tabs_.size()-1);
    if(Index(selected_)==tabs_.size()){selected_=tabs_.front().id;visual_valid_=false;motion_seconds_=kSelectionSeconds;}
    if(Index(focused_)==tabs_.size())focused_=selected_;
    return true;
  }
  const std::vector<Tab>& tabs()const{return tabs_;}
  size_t selected_index()const{return Index(selected_);}
  std::string_view selected_id()const{return selected_;}
  bool Select(std::string_view id) {
    if(Index(id)==tabs_.size())return false;
    if(selected_!=id){
      const auto before=Index(selected_),after=Index(id);
      const auto distance=before>after?before-after:after-before;
      motion_from_=visual_;
      motion_seconds_=visual_valid_&&distance==1?0:kSelectionSeconds;
    }
    selected_=focused_=id;return true;
  }
  bool MoveFocus(int direction) {
    if(tabs_.empty()||!direction)return false;
    auto index=Index(focused_);if(index==tabs_.size())index=0;
    index=direction<0?(index+tabs_.size()-1)%tabs_.size():(index+1)%tabs_.size();
    focused_=tabs_[index].id;return true;
  }
  bool ActivateFocused(){return Select(focused_);}
  bool SelectNeighbor(int direction){focused_=selected_;return MoveFocus(direction)&&ActivateFocused();}
  bool MoreBefore()const{return visible_count_&&first_visible_>0;}
  bool MoreAfter()const{return visible_count_&&first_visible_+visible_count_<tabs_.size();}
  // A bounded viewport supports variable page counts; focus scrolls into view.
  std::vector<TabCell> Layout(Rect bounds,size_t capacity=9)const {
    if(tabs_.empty()||!capacity||!std::isfinite(bounds.w)||bounds.w<=0){visible_count_=0;return {};}
    const auto fitting=size_t(std::max(0.f,std::floor((bounds.w+3.f)/123.f)));
    auto count=std::min({capacity,tabs_.size(),fitting});
    if(!count){visible_count_=0;return {};}
    auto focused=Index(focused_);
    first_visible_=std::min(first_visible_,tabs_.size()-count);
    if(focused<first_visible_)first_visible_=focused;
    else if(focused>=first_visible_+count)first_visible_=focused-count+1;
    visible_count_=count;
    const auto first=first_visible_;
    float gap=3, width=std::min(220.f,(bounds.w-gap*float(count-1))/float(count));
    if(width<24)return {};
    std::vector<TabCell> cells;
    for(size_t i=0;i<count;++i)cells.push_back({first+i,{bounds.x+float(i)*(width+gap),bounds.y,width,bounds.h},tabs_[first+i].id==selected_,tabs_[first+i].id==focused_});
    return cells;
  }
  // Binding supplies native donor artwork/text; this renderer is also usable by
  // pause pages without owning or knowing their gameplay controller.
  template<class Paper,class Label>
  void Paint(Rect bounds,size_t capacity,Paper paper,Label label,float font_size=18.f)const {
    auto cells=Layout(bounds,capacity);
    for(size_t slot=0;slot<cells.size();++slot){const auto& cell=cells[slot];const auto& tab=tabs_[cell.index];
      auto shape=cell.bounds;
      if(cell.selected){
        if(visual_valid_&&motion_seconds_<kSelectionSeconds){
          const float t=float(motion_seconds_/kSelectionSeconds),ease=1-(1-t)*(1-t)*(1-t);
          shape.x=motion_from_.x+(shape.x-motion_from_.x)*ease;
          shape.w=motion_from_.w+(shape.w-motion_from_.w)*ease;
        }
        visual_=shape;visual_valid_=true;
      }
      paper(slot,shape,cell.selected?0xff007au:0x302b30u);
      auto text=cell.bounds;text.x+=8;text.w-=16;text.y+=tab.locked?0:5;text.h=tab.locked?26:31;
      label(slot,0,text,font_size,tab.label,cell.selected?0xffffffu:tab.locked?0xc9c2c9u:0xffffffu);
      if(tab.locked){auto lock=cell.bounds;lock.y+=29;lock.h=17;label(slot,1,lock,11.f,tab.unavailable_label,0xc9c2c9u);}
    }
  }
};

struct Header {
  std::string title,status;
  bool changed=false;
  Rect bounds{42,25,1196,82};
  template<class Paper,class Label,class CarIdentity>
  void Paint(Paper paper,Label label,CarIdentity identity)const {
    paper(bounds,0x302b30u);
    label(0,{bounds.x+22,bounds.y+3,735,45},31.f,title,0xffffffu,0u);
    label(1,{bounds.x+bounds.w-420,bounds.y+10,396,33},18.f,status,changed?0xff83bfu:0xffffffu,2u);
    identity({bounds.x+22,bounds.y+51,154,26},{bounds.x+188,bounds.y+51,bounds.w-524,26},15.f);
  }
  Rect CurrentClassBounds()const{return {bounds.x+bounds.w-260,bounds.y+51,236,26};}
};
}
