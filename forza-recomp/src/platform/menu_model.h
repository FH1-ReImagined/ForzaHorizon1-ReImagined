#pragma once

// Original native menu state and input handling. No game or renderer dependencies.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace forza::menu {

struct Rect {
  double x = 0, y = 0, width = 0, height = 0;
};

struct Tile {
  std::string id;
  std::string label;
  Rect bounds;
  std::string action;  // Empty means an informational, nonfocusable tile.
  bool visible = true;
  bool enabled = true;
};

struct Page {
  std::string id;
  std::string label;
  std::vector<Tile> tiles;
  bool visible = true;
  bool enabled = true;
};

inline bool Available(const Page& page) { return page.visible && page.enabled; }

inline bool Focusable(const Tile& tile) {
  const auto& r = tile.bounds;
  return tile.visible && tile.enabled && !tile.action.empty() &&
         std::isfinite(r.x) && std::isfinite(r.y) &&
         std::isfinite(r.width) && std::isfinite(r.height) &&
         r.width > 0 && r.height > 0 &&
         std::isfinite(r.x + r.width) && std::isfinite(r.y + r.height);
}

enum class Direction { Up, Down, Left, Right };
using ButtonMask = uint16_t;
enum class Button : ButtonMask {
  Up = 1u << 0, Down = 1u << 1, Left = 1u << 2, Right = 1u << 3,
  PreviousPage = 1u << 4, NextPage = 1u << 5,
  Confirm = 1u << 6, Back = 1u << 7,
};
constexpr ButtonMask Mask(Button button) { return static_cast<ButtonMask>(button); }
constexpr ButtonMask operator|(Button a, Button b) { return Mask(a) | Mask(b); }
constexpr ButtonMask operator|(ButtonMask a, Button b) { return a | Mask(b); }
inline constexpr ButtonMask kAllButtons = 0xff;
inline constexpr ButtonMask kNavigationButtons = 0x3f;

struct RepeatTiming {
  double delay_seconds = 0.35;
  double interval_seconds = 0.10;
};

// The caller supplies elapsed monotonic seconds, not a wall-clock timestamp.
// Invalid deltas accrue no repeat time. A long stall emits at most one pulse
// per button, then retains the repeat phase without a catch-up burst.
class InputGate {
 public:
  explicit InputGate(RepeatTiming timing = {},
                     ButtonMask repeating = kNavigationButtons)
      : timing_(timing), repeating_(repeating & kAllButtons) {
    if (!std::isfinite(timing.delay_seconds) || timing.delay_seconds < 0 ||
        !std::isfinite(timing.interval_seconds) || timing.interval_seconds <= 0)
      throw std::invalid_argument("Invalid menu repeat timing");
  }

  // Each button must be observed released before it may generate a new edge.
  // This also clears repeat clocks when a child screen owns input.
  void RequireRelease() {
    blocked_ = kAllButtons;
    previous_ = 0;
    remaining_.fill(0);
  }

  ButtonMask Update(ButtonMask held, double delta_seconds, ButtonMask neutral = 0) {
    held &= kAllButtons;
    blocked_ &= held;
    // Release protection observes physical state before neutralization, so
    // opposing held buttons cannot bypass the entry/return guard.
    held &= static_cast<ButtonMask>(~(blocked_ | neutral));
    const double elapsed = std::isfinite(delta_seconds) && delta_seconds > 0
                               ? delta_seconds : 0;
    ButtonMask pulses = 0;
    for (unsigned index = 0; index < remaining_.size(); ++index) {
      const auto bit = static_cast<ButtonMask>(1u << index);
      if (!(held & bit)) {
        remaining_[index] = 0;
      } else if (!(previous_ & bit)) {
        pulses |= bit;
        remaining_[index] = timing_.delay_seconds;
      } else if ((repeating_ & bit) && elapsed > 0) {
        // Tolerance is relative to the configured period, not frame duration.
        const double tolerance = timing_.interval_seconds * 1e-10;
        if (elapsed >= remaining_[index] ||
            remaining_[index] - elapsed <= tolerance) {
          pulses |= bit;
          const double overdue = (std::max)(0.0, elapsed - remaining_[index]);
          remaining_[index] = timing_.interval_seconds -
                              std::fmod(overdue, timing_.interval_seconds);
        } else {
          remaining_[index] -= elapsed;
        }
      }
    }
    previous_ = held;
    return pulses;
  }

 private:
  RepeatTiming timing_;
  ButtonMask repeating_;
  ButtonMask previous_ = 0;
  ButtonMask blocked_ = 0;
  std::array<double, 8> remaining_{};
};

struct UpdateResult {
  std::optional<std::string> action;
  bool back = false;
  bool changed = false;
};

class Controller {
 public:
  explicit Controller(RepeatTiming timing = {}) : gate_(timing) { Enter(); }

  // IDs must be nonempty and unique (tile IDs within their page). Descriptors
  // are owned values; callers may immediately discard their source data.
  // A refresh preserves valid selection by ID and never resets the input gate.
  void SetPages(std::vector<Page> pages) {
    std::unordered_set<std::string> page_ids;
    for (const auto& page : pages) {
      if (page.id.empty() || !page_ids.insert(page.id).second)
        throw std::invalid_argument("Menu page IDs must be nonempty and unique");
      std::unordered_set<std::string> tile_ids;
      for (const auto& tile : page.tiles)
        if (tile.id.empty() || !tile_ids.insert(tile.id).second)
          throw std::invalid_argument("Menu tile IDs must be nonempty and unique per page");
    }
    pages_ = std::move(pages);
    for (auto it = focus_.begin(); it != focus_.end();) {
      if (!page_ids.contains(it->first)) it = focus_.erase(it);
      else ++it;
    }
    const Page* current = FindPage(page_id_);
    if (!current || !Available(*current)) {
      page_id_.clear();
      for (const auto& page : pages_)
        if (Available(page)) { page_id_ = page.id; break; }
    }
    // Reconcile every page, including temporarily unavailable pages, so a
    // removed tile cannot leave a stale focus ID waiting for a tab switch.
    for (const auto& page : pages_) ReconcileFocus(page);
  }

  // Renderer iteration is read-only. References expire at the next SetPages;
  // selection and action accessors return owning IDs that survive refreshes.
  const std::vector<Page>& pages() const { return pages_; }
  std::optional<std::string> page_id() const {
    return page_id_.empty() ? std::nullopt : std::optional{page_id_};
  }
  std::optional<std::string> focused_tile_id() const {
    auto it = focus_.find(page_id_);
    return it == focus_.end() || it->second.empty()
               ? std::nullopt : std::optional{it->second};
  }
  bool suspended() const { return suspended_; }

  void Enter() { suspended_ = false; gate_.RequireRelease(); }
  void Suspend() { suspended_ = true; gate_.RequireRelease(); }
  void Resume() { suspended_ = false; gate_.RequireRelease(); }

  bool SelectPage(std::string_view id) {
    if (suspended_) return false;
    const Page* page = FindPage(id);
    if (!page || !Available(*page) || page_id_ == id) return false;
    page_id_ = page->id;
    ReconcileFocus(*page);
    return true;
  }

  bool FocusTile(std::string_view id) {
    if (suspended_) return false;
    const Page* page = FindPage(page_id_);
    if (!page) return false;
    for (const auto& tile : page->tiles) {
      if (tile.id != id || !Focusable(tile)) continue;
      auto& focus = focus_[page_id_];
      if (focus == id) return false;
      focus = tile.id;
      return true;
    }
    return false;
  }

  bool ChangePage(int direction) {
    if (suspended_ || direction == 0 || page_id_.empty()) return false;
    const auto current = std::find_if(pages_.begin(), pages_.end(),
        [&](const Page& page) { return page.id == page_id_; });
    auto index = std::distance(pages_.begin(), current);
    const auto end = static_cast<decltype(index)>(pages_.size());
    for (index += direction > 0 ? 1 : -1; index >= 0 && index < end;
         index += direction > 0 ? 1 : -1) {
      if (Available(pages_[index])) return SelectPage(pages_[index].id);
    }
    return false;
  }

  bool Move(Direction direction) {
    if (suspended_) return false;
    const Page* page = FindPage(page_id_);
    if (!page) return false;
    const auto focus = focused_tile_id();
    const Tile* source = nullptr;
    for (const auto& tile : page->tiles)
      if (focus && tile.id == *focus) { source = &tile; break; }
    if (!source) return false;

    // Prefer the same horizontal/vertical beam. Within it, choose nearest
    // facing edges, then closest centers; outside it, choose shortest distance
    // between rectangles. IDs break geometric ties independently of list order.
    using Rank = std::tuple<bool, double, double, double, std::string>;
    std::optional<Rank> best;
    const Tile* target = nullptr;
    const auto origin = Project(source->bounds, direction);
    for (const auto& tile : page->tiles) {
      if (tile.id == source->id || !Focusable(tile)) continue;
      const auto candidate = Project(tile.bounds, direction);
      const double forward = candidate.center - origin.center;
      if (!(forward > 0)) continue;
      const double along = (std::max)(0.0, candidate.leading - origin.trailing);
      const double across = (std::max)({0.0, candidate.low - origin.high,
                                       origin.low - candidate.high});
      const bool outside = candidate.low >= origin.high ||
                           candidate.high <= origin.low;
      const Rank rank{outside, outside ? std::hypot(along, across) : along,
                      std::abs(candidate.cross_center - origin.cross_center),
                      forward, tile.id};
      if (!best || rank < *best) { best = rank; target = &tile; }
    }
    return target && FocusTile(target->id);
  }

  std::optional<std::string> Activate() const {
    if (suspended_) return std::nullopt;
    const Page* page = FindPage(page_id_);
    const auto focus = focused_tile_id();
    if (page && focus)
      for (const auto& tile : page->tiles)
        if (tile.id == *focus && Focusable(tile)) return tile.action;
    return std::nullopt;
  }

  UpdateResult Update(ButtonMask held, double delta_seconds) {
    if (suspended_) return {};
    // Opposite directions are neutral, including their repeat clocks. Releasing
    // either direction gives the remaining held direction a fresh edge.
    ButtonMask neutral = 0;
    for (const ButtonMask pair : {Button::Up | Button::Down,
                                 Button::Left | Button::Right,
                                 Button::PreviousPage | Button::NextPage})
      if ((held & pair) == pair) neutral |= pair;
    const auto pulses = gate_.Update(held, delta_seconds, neutral);
    UpdateResult result;
    if (pulses & Mask(Button::Back)) { result.back = true; return result; }
    // Only one interaction per update. Navigation consumes a simultaneous
    // confirm edge, preventing accidental activation of the newly focused tile.
    if (pulses & Mask(Button::PreviousPage)) result.changed = ChangePage(-1);
    else if (pulses & Mask(Button::NextPage)) result.changed = ChangePage(1);
    else if (pulses & Mask(Button::Up)) result.changed = Move(Direction::Up);
    else if (pulses & Mask(Button::Down)) result.changed = Move(Direction::Down);
    else if (pulses & Mask(Button::Left)) result.changed = Move(Direction::Left);
    else if (pulses & Mask(Button::Right)) result.changed = Move(Direction::Right);
    else if (pulses & Mask(Button::Confirm)) result.action = Activate();
    return result;
  }

 private:
  struct Projection { double leading, trailing, center, low, high, cross_center; };
  static Projection Project(const Rect& rect, Direction direction) {
    const bool horizontal = direction == Direction::Left || direction == Direction::Right;
    const bool reverse = direction == Direction::Left || direction == Direction::Up;
    const double leading = horizontal ? rect.x : rect.y;
    const double length = horizontal ? rect.width : rect.height;
    const double low = horizontal ? rect.y : rect.x;
    const double cross_length = horizontal ? rect.height : rect.width;
    const double center = leading + length * 0.5;
    return {reverse ? -(leading + length) : leading,
            reverse ? -leading : leading + length,
            reverse ? -center : center, low, low + cross_length,
            low + cross_length * 0.5};
  }
  const Page* FindPage(std::string_view id) const {
    for (const auto& page : pages_) if (page.id == id) return &page;
    return nullptr;
  }
  void ReconcileFocus(const Page& page) {
    auto& focus = focus_[page.id];
    for (const auto& tile : page.tiles)
      if (tile.id == focus && Focusable(tile)) return;
    focus.clear();
    for (const auto& tile : page.tiles)
      if (Focusable(tile)) { focus = tile.id; return; }
  }

  std::vector<Page> pages_;
  std::string page_id_;
  std::unordered_map<std::string, std::string> focus_;
  InputGate gate_;
  bool suspended_ = false;
};

}  // namespace forza::menu
