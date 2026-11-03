#pragma once
#include <cstdint>
#include <memory>
namespace rex::ui { class Window; }
namespace forza::auto_pause {
class WindowObserver {
 public:
  explicit WindowObserver(rex::ui::Window* window);
  ~WindowObserver();
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
void World(uint32_t address);
bool Poll(uint8_t* base, uint16_t buttons, uint32_t user_mask);
void Cancel();
}
