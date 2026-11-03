#include "../src/platform/fh1_menu_components.h"
#include <cstdlib>
#include <iostream>
using namespace forza::fh1_menu_components;
void Check(bool condition,const char* message){if(!condition){std::cerr<<message<<'\n';std::exit(1);}}
int main(){
  {
    const std::array<float,16> identity{1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};
    const float width=500,height=216,shear=kMenuShear*height/width;
    const auto uv=UprightShearUV(identity,shear);
    for(float u:{0.f,.25f,.5f,1.f})for(float v:{0.f,.5f,1.f}){
      const auto screen_x=width*u+kMenuShear*height*(v-.5f);
      const auto sample_u=uv[0]*u+uv[4]*v+uv[12];
      Check(std::fabs(screen_x-width*sample_u)<.0001f,"Map texture must cancel the quad shear at every height");
      Check(uv[1]*u+uv[5]*v+uv[13]==v,"Map vertical sampling must stay upright");
    }
    const auto tab=MenuQuad({64,112,192,42});
    Check(std::fabs(tab.w-kMenuScaleX*192)<.0001f,"Tab geometry must use the shared composition width");
  }
  const auto grid=SliderGridStart(188.55,0,.1);
  Check(std::fabs(grid-188.6)<1e-8,"Choice grid must align to display precision, not raw minimum");
  Check(std::fabs((grid+489*1.3)-824.3)<1e-8,"Spring choices must avoid half-decimal rounding midpoints");
  const std::vector<float> choices{-1,15,20,29.9f,30,30.00001f,30.1f,55};
  Check(SliderNeighbor(choices,0,30,.025f,1)==6,"Stock right must step above stock");
  Check(SliderNeighbor(choices,0,30,.025f,-1)==3,"Stock left must step below stock");
  Check(SliderNeighbor(choices,4,30,.025f,1)==6,"Saved near-duplicate must not consume an input step");
  Check(SliderNeighbor(choices,1,30,.025f,-1)==1,"Minimum must clamp instead of resetting to stock");
  Check(SliderNeighbor(choices,7,30,.025f,1)==7,"Maximum must clamp");
  Check(SliderNeighbor(choices,0,15,.025f,-1)==0,"Bounded stock must preserve its sentinel");
  Check(!SliderNeighbor(choices,99,30,.025f,1),"Invalid slider selection must fail closed");
  TabBar tabs;
  Check(!tabs.Select("missing")&&tabs.Layout({0,0,100,40}).empty(),"Empty navigation must remain inert");
  Check(tabs.SetTabs({{"a","A"},{"locked","Locked",true},{"c","C"}}),"Valid tabs rejected");
  tabs.SelectNeighbor(-1);Check(tabs.selected_id()=="c","Previous must wrap");
  tabs.SelectNeighbor(1);Check(tabs.selected_id()=="a","Next must wrap");
  tabs.MoveFocus(1);Check(tabs.selected_id()=="a","Directional focus must not activate a page");
  tabs.ActivateFocused();Check(tabs.selected_id()=="locked","Locked page explanation must remain reachable");
  Check(tabs.SetTabs({{"c","Renamed"},{"locked","Still locked",true}})&&tabs.selected_id()=="locked","Reorder/rename lost stable selection");
  Check(!tabs.SetTabs({{"x","X"},{"x","Duplicate"}})&&tabs.selected_id()=="locked","Invalid update corrupted selection");
  Check(tabs.SetTabs({{"c","C"}})&&tabs.selected_id()=="c","Removed selection did not fall back");
  std::vector<Tab> many;for(int i=0;i<12;++i)many.push_back({std::to_string(i),"Page"});
  tabs.SetTabs(many);tabs.Select("11");auto cells=tabs.Layout({44,120,1192,48},9);
  Check(cells.size()==9&&cells.back().index==11&&cells.back().selected,"Viewport did not expose selected page");
  Check(cells.front().bounds.x>=44&&cells.back().bounds.x+cells.back().bounds.w<=1236.01,"Viewport exceeded supplied bounds");
  Check(tabs.MoreBefore()&&!tabs.MoreAfter(),"Last page must indicate earlier hidden tabs");
  tabs.Select("10");cells=tabs.Layout({44,120,1192,48},9);
  Check(cells.front().index==3,"Moving within the visible window must not scroll it");
  tabs.Select("2");cells=tabs.Layout({44,120,1192,48},9);
  Check(cells.front().index==2&&tabs.MoreBefore()&&tabs.MoreAfter(),"Crossing the left edge must scroll one tab");
  tabs.Select("11");cells=tabs.Layout({0,0,500,42},9);
  Check(cells.size()==4&&cells.back().selected,"Width must bound tab count and keep selection visible");
  tabs.Select("0");cells=tabs.Layout({0,0,500,42},9);
  Check(cells.front().index==0&&!tabs.MoreBefore()&&tabs.MoreAfter(),"Wrap to first must restore the first window");
  Check(tabs.Layout({0,0,2,48},9).empty(),"Unsupported narrow layout must fail closed");
  Check(!tabs.MoreBefore()&&!tabs.MoreAfter(),"Empty viewport must hide overflow indicators");
  TabBar community;community.SetTabs({{"community","COMMUNITY",true,"Coming soon"}});
  std::string unavailable;
  community.Paint({0,0,220,48},9,[](size_t,Rect,uint32_t){},
    [&](size_t,unsigned anchor,Rect,float,const std::string& text,uint32_t){if(anchor==1)unavailable=text;});
  Check(unavailable=="Coming soon","Unavailable tabs must expose their explicit availability label");
  Header header;const auto badge=header.CurrentClassBounds();
  header.Paint([](Rect,uint32_t){},[](unsigned,Rect,float,const std::string&,uint32_t,unsigned){},
    [&](Rect make,Rect model,float){Check(make.x+make.w<=model.x&&model.x+model.w<badge.x,"Car class must not overlap manufacturer/model");});
  Check(badge.x+badge.w<=header.bounds.x+header.bounds.w&&badge.y+badge.h<=header.bounds.y+header.bounds.h,"Class badge must remain inside header");
  TabBar animated;animated.SetTabs({{"home","HOME"},{"cars","CARS"},{"settings","SETTINGS"}});
  float active_x=0;
  const auto paint=[&]{animated.Paint({64,112,580,42},3,
    [&](size_t,Rect r,uint32_t color){if(color==0xff007a)active_x=r.x;},
    [](size_t,unsigned,Rect,float,const std::string&,uint32_t){});};
  paint();const auto first_x=active_x;
  animated.Select("cars");Check(animated.selected_id()=="cars","Animation must not delay logical selection");
  paint();Check(active_x==first_x,"A transition must begin at the prior visual location");
  animated.Advance(.075);paint();const auto halfway=active_x;
  Check(halfway>first_x&&halfway<animated.Layout({64,112,580,42},3)[1].bounds.x,"Selection motion must interpolate");
  animated.Select("home");paint();Check(active_x==halfway,"Interrupted motion must continue from its displayed position");
  animated.Advance(10);paint();Check(active_x==first_x,"Dropped frames must settle without queued animations");
  animated.SelectNeighbor(-1);paint();
  Check(animated.selected_id()=="settings"&&active_x==animated.Layout({64,112,580,42},3)[2].bounds.x,"Wrap must snap instead of sliding across unrelated tabs");
  for(auto size:{std::pair{1280u,720u},std::pair{1280u,800u},std::pair{2560u,1080u}}){
    const auto r=BackdropBounds(size.first,size.second);
    const auto scale=std::min(float(size.first)/1280,float(size.second)/720);
    Check(r.w>float(size.first)/scale&&r.h>float(size.second)/scale,"Backdrop bleed must cover the physical viewport at each aspect ratio");
  }
}
