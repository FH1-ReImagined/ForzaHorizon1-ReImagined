// Standalone corruption/replacement and size checks. No game files, GPU or Vulkan SDK required.
#include <rex/graphics/vulkan/driver_cache_file.h>
#include <iostream>
#include <map>
#include <stdexcept>
#include <thread>
using namespace rex::graphics::vulkan::driver_cache_file;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("line " + std::to_string(__LINE__) + ": " #x); } while (false)

namespace {
Identity TestIdentity() {
  Identity id{0x10de, 0x1234, 17, 0x300000004, {}};
  for (size_t i = 0; i < 16; ++i) id.uuid[i] = uint8_t(i * 13);
  return id;
}
// Driver data as vkGetPipelineCacheData returns it: the VERSION_ONE header, then the driver's.
std::vector<uint8_t> Payload(const Identity& id, size_t size, uint8_t fill) {
  std::vector<uint8_t> p(size, fill);
  Put32(p.data(), 32); Put32(p.data() + 4, 1);
  Put32(p.data() + 8, id.vendor); Put32(p.data() + 12, id.device);
  std::copy(id.uuid.begin(), id.uuid.end(), p.begin() + 16);
  return p;
}
bool SameEntry(const Entry& a, const Entry& b) {
  return a.key == b.key && a.fresh_size == b.fresh_size && a.data && b.data && *a.data == *b.data;
}
std::vector<uint8_t> ReadBytes(const std::filesystem::path& path) {
  std::ifstream f(path, std::ios::binary);
  return std::vector<uint8_t>(std::istreambuf_iterator<char>(f), {});
}
void WriteBytes(const std::filesystem::path& path, const std::vector<uint8_t>& bytes) {
  std::ofstream(path, std::ios::binary | std::ios::trunc)
      .write(reinterpret_cast<const char*>(bytes.data()), std::streamsize(bytes.size()));
}

// A model of a driver's VkPipelineCache: entries keyed by the driver's own pipeline key, all
// written back by GetData, none ever evicted (the behaviour of the drivers seen). A pipeline
// is found again only if the driver computes the same key for it.
struct ModelCache {
  std::map<uint64_t, size_t> entries;  // driver key -> compiled size
};
std::vector<uint8_t> ModelData(const Identity& id, const ModelCache& cache) {
  std::vector<uint8_t> data = Payload(id, 32, 0);
  for (const auto& [key, size] : cache.entries) {
    uint8_t h[12];
    Put64(h, key); Put32(h + 8, uint32_t(size));
    data.insert(data.end(), h, h + 12);
    data.insert(data.end(), size, uint8_t(key));
  }
  return data;
}
ModelCache ModelLoad(const std::vector<uint8_t>* data) {
  ModelCache cache;
  if (!data) return cache;
  for (size_t at = 32; at + 12 <= data->size();) {
    const size_t size = U32(data->data() + at + 8);
    cache.entries[U64(data->data() + at)] = size;
    at += 12 + size;
  }
  return cache;
}
// Creates pipeline `pipeline` in `cache`; the driver keys it `driver_key`. Returns true if found.
bool ModelCreate(ModelCache& cache, uint64_t driver_key, size_t compiled_size) {
  if (cache.entries.count(driver_key)) return true;
  cache.entries[driver_key] = compiled_size;
  return false;
}
}  // namespace

int main() {
  const auto temporary = std::filesystem::weakly_canonical(std::filesystem::temp_directory_path());
  auto root = temporary /
      ("fh1-driver-cache-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  bool created = false;
  auto cleanup = [&] {
    if (created && std::filesystem::weakly_canonical(root).parent_path() == temporary)
      std::filesystem::remove_all(root);
  };
  try {
    const Identity id = TestIdentity();
    CHECK(std::filesystem::create_directory(root)); created = true;
    const auto path = root / "cache.vkpc";

    // Entries: one per pipeline description.
    std::vector<Entry> entries;
    for (uint64_t k = 0; k < 4; ++k) {
      auto data = Payload(id, 100 + 37 * size_t(k), uint8_t(0xa0 + k));
      const size_t size = data.size();
      entries.push_back(MakeEntry(0x1000 + k, std::move(data), size));
    }
    CHECK(!ValidPayload(Payload(id, 32, 0), id));  // a header alone holds no pipeline
    auto invalid = Payload(id, 128, 1); Put32(invalid.data(), 1000); CHECK(!ValidPayload(invalid, id));
    invalid = Payload(id, 128, 1); Put32(invalid.data() + 4, 2); CHECK(!ValidPayload(invalid, id));
    invalid = Payload(id, 128, 1); ++invalid[20]; CHECK(!ValidPayload(invalid, id));

    CHECK(Read(path, id).entries.empty() && !Read(path, id).rejected);

    // #443: adding only omitted entries must not rewrite the bounded subset.
    // A tiny budget exercises the same writer as the 512 MiB production limit.
    {
      const auto bounded = root / "bounded.vkpc";
      WriteState state;
      const uint64_t budget = kHeaderSize + kEntryHeaderSize + entries[0].size();
      auto save = [&](const std::vector<Entry>& list) {
        return Write(bounded, list, id, budget, &state);
      };
      CHECK(save(entries).written);
      const auto stamp = std::filesystem::file_time_type::clock::now() - std::chrono::hours(24);
      std::filesystem::last_write_time(bounded, stamp);
      auto more = entries;
      more.push_back(MakeEntry(0x2000, Payload(id, 200, 9), 200));
      auto skipped = save(more);
      CHECK(skipped.unchanged && !skipped.written && skipped.entries == 1 && skipped.skipped == 4);
      CHECK(std::filesystem::last_write_time(bounded) == stamp);
      // Separately loaded but identical bytes also skip; no pointer-only decision.
      more[0] = Read(bounded, id).entries[0];
      CHECK(save(more).unchanged);
      // Same-sized data and even a checksum collision still require replacement.
      auto changed = more;
      changed[0] = MakeEntry(changed[0].key, Payload(id, changed[0].size(), 7), 100);
      changed[0].checksum = more[0].checksum;  // force a collision for the comparison
      CHECK(save(changed).written);
      CHECK(save(more).written);
      changed = more; ++changed[0].fresh_size;
      CHECK(save(changed).written);
      changed = more; --changed[0].key;
      CHECK(save(changed).written);
      // A new earlier key displaces a retained entry; removing it restores the old one.
      changed = more;
      changed.insert(changed.begin(), MakeEntry(1, Payload(id, 100, 8), 100));
      CHECK(save(changed).written && Read(bounded, id).entries[0].key == 1);
      CHECK(save(more).written);
      // A smaller entry can fill leftover budget even when larger entries were omitted.
      const uint64_t larger = budget + kEntryHeaderSize + 40;
      changed = more;
      changed.push_back(MakeEntry(0x3000, Payload(id, 40, 8), 40));
      CHECK(Write(bounded, changed, id, larger, &state).written);
      CHECK(Read(bounded, id).entries.size() == 2);
      // State is local to a destination, identity and cache lifetime.
      CHECK(Write(root / "other.vkpc", changed, id, larger, &state).written);
      CHECK(Write(bounded, changed, id, larger, &state).written);
      state = {};
      CHECK(Write(bounded, changed, id, larger, &state).written);
      auto other_id = id; ++other_id.driver;
      CHECK(Write(bounded, changed, other_id, larger, &state).written);
      CHECK(!Read(bounded, other_id).rejected);
      CHECK(Write(bounded, changed, id, larger, &state).written);
      changed.clear(); more.clear();
      CHECK(!state.entries.back().data.lock());  // snapshots do not retain old payloads
      CHECK(save(entries).written);
      // A failed atomic replacement must not mark the changed contents as saved.
      const auto blocked = root / "blocked.vkpc";
      std::filesystem::create_directory(blocked);
      CHECK(!Write(blocked, entries, id, budget, &state).written);
      std::filesystem::remove(blocked);
      CHECK(Write(blocked, entries, id, budget, &state).written);
      CHECK(Write(blocked, entries, id, budget, &state).unchanged);
    }
    auto written = Write(path, entries, id);
    CHECK(written.written && written.bytes == std::filesystem::file_size(path));
    auto read = Read(path, id);
    CHECK(!read.rejected && read.entries.size() == entries.size());
    for (size_t i = 0; i < entries.size(); ++i) CHECK(SameEntry(read.entries[i], entries[i]));

    // Any other identity: nothing is read.
    for (int field = 0; field < 5; ++field) {
      Identity other = id;
      if (field == 0) ++other.vendor;
      if (field == 1) ++other.device;
      if (field == 2) ++other.driver;
      if (field == 3) ++other.abi;
      if (field == 4) ++other.uuid[5];
      read = Read(path, other);
      CHECK(read.entries.empty() && read.rejected);
    }

    // Every single-byte corruption and every truncation: never a damaged entry, only a prefix
    // of the entries before the damage, and the file is reported for rewriting.
    const auto file = ReadBytes(path);
    const auto damaged = root / "damaged.vkpc";
    for (size_t i = 0; i < file.size(); ++i) {
      auto broken = file; broken[i] ^= 1;
      WriteBytes(damaged, broken);
      read = Read(damaged, id);
      CHECK(read.rejected && read.entries.size() < entries.size());
      for (size_t e = 0; e < read.entries.size(); ++e) CHECK(SameEntry(read.entries[e], entries[e]));
    }
    for (size_t n = 0; n < file.size(); ++n) {
      WriteBytes(damaged, std::vector<uint8_t>(file.begin(), file.begin() + n));
      read = Read(damaged, id);
      CHECK(read.entries.size() < entries.size() && (n == 0 || read.rejected));
      for (size_t e = 0; e < read.entries.size(); ++e) CHECK(SameEntry(read.entries[e], entries[e]));
    }
    auto appended = file; appended.push_back(0);
    WriteBytes(damaged, appended);
    read = Read(damaged, id);
    CHECK(read.rejected && read.entries.size() == entries.size());
    // The format-1 file (one cache, one checksummed payload) is not read.
    auto format1 = file; Put32(format1.data() + 8, 1);
    WriteBytes(damaged, format1);
    CHECK(Read(damaged, id).entries.empty() && Read(damaged, id).rejected);

    // Size limits: an oversized file is not read. A save retains complete entries
    // within its budget, rather than losing all persistence when one more is added.
    {
      std::ofstream oversized(root / "oversized", std::ios::binary);
      oversized.seekp(std::streamoff(kMaxFileSize + 1)); oversized.put(0);
    }
    CHECK(Read(root / "oversized", id).entries.empty() && Read(root / "oversized", id).rejected);
    std::filesystem::remove(root / "oversized");
    {
      // Small budgets exercise the real encoder, filesystem replacement and reader
      // without writing a 512 MiB fixture. Exactly two entries fit including headers.
      const uint64_t budget = kHeaderSize + 2 * (kEntryHeaderSize + 128);
      std::vector<Entry> many;
      for (uint64_t key = 0; key < 6; ++key)
        many.push_back(MakeEntry(key, Payload(id, 128, uint8_t(key)), 128));
      for (int session = 0; session < 3; ++session) {
        const auto bounded = Write(path, many, id, budget);
        CHECK(bounded.written && bounded.entries == 2 && bounded.skipped == 4);
        CHECK(bounded.bytes == budget && std::filesystem::file_size(path) == budget);
        read = Read(path, id);
        CHECK(!read.rejected && read.entries.size() == 2);
        for (size_t i = 0; i < 2; ++i) CHECK(SameEntry(read.entries[i], many[i]));
      }
      // A large first entry does not prevent a later small entry from fitting.
      many.insert(many.begin(), MakeEntry(99, Payload(id, 512, 7), 512));
      const auto bounded = Write(path, many, id, budget - 1);
      CHECK(bounded.written && bounded.entries == 1 && bounded.skipped == 6);
      read = Read(path, id);
      CHECK(!read.rejected && read.entries.size() == 1 && SameEntry(read.entries[0], many[1]));
      CHECK(bounded.bytes == kHeaderSize + kEntryHeaderSize + 128);
      const auto before = ReadBytes(path);
      CHECK(!Write(path, many, id, kHeaderSize - 1).written);
      CHECK(ReadBytes(path) == before);
      // Omitted entries remain available to the next save with a larger budget.
      const auto full = Write(path, many, id);
      CHECK(full.written && full.entries == many.size() && full.skipped == 0);
      CHECK(Read(path, id).entries.size() == many.size());
      CHECK(!ValidPayload(Payload(id, kMaxEntrySize + 1, 7), id));
    }

    // Failed writes keep the old file; invalid entries are left out.
    std::ofstream(root / "blocked").put('x');
    CHECK(!Write(root / "blocked" / "child", entries, id).written);
    auto with_invalid = entries;
    with_invalid.push_back(MakeEntry(0x2000, invalid, invalid.size()));
    CHECK(Write(path, with_invalid, id).written && Read(path, id).entries.size() == entries.size());

    // Concurrent replacement: a reader sees one whole version or nothing.
    auto replacement = entries;
    replacement.pop_back();
    CHECK(Write(path, entries, id).written);
    std::atomic<bool> finish{false}, failed{false};
    std::thread reader([&] {
      while (!finish) {
        const auto current = Read(path, id);
        // Windows may deny a racing open during replacement. A missing cache
        // is a supported fallback; a partial or mixed result is not.
        if (current.entries.empty()) continue;
        const auto& expect = current.entries.size() == entries.size() ? entries : replacement;
        if (current.rejected || current.entries.size() != expect.size()) { failed = true; continue; }
        for (size_t i = 0; i < expect.size(); ++i)
          if (!SameEntry(current.entries[i], expect[i])) failed = true;
      }
    });
    WriteState concurrent_state;  // one storage writer, concurrent file readers
    for (size_t i = 0; i < 50; ++i) {
      const auto& list = i % 2 ? entries : replacement;
      const auto saved = Write(path, list, id, kMaxFileSize, &concurrent_state);
      // On Windows a reader can temporarily deny atomic replacement. The next
      // attempt must retry; only a successful/unchanged snapshot may skip.
      if (saved.written || saved.unchanged) {
        if (!Write(path, list, id, kMaxFileSize, &concurrent_state).unchanged) failed = true;
      }
    }
    finish = true; reader.join(); CHECK(!failed);

#ifdef _WIN32
    // An existing reader that denies deletion forces atomic replacement to fail.
    // Retain the exact old bytes and remove the failed save's temporary file.
    {
      const auto before = ReadBytes(path);
      HANDLE held = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                                OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
      CHECK(held != INVALID_HANDLE_VALUE);
      const auto blocked = Write(path, entries, id, kHeaderSize + kEntryHeaderSize + 128);
      CloseHandle(held);
      CHECK(!blocked.written && ReadBytes(path) == before);
      for (const auto& item : std::filesystem::directory_iterator(root))
        CHECK(item.path().filename().string().find("cache.vkpc.tmp-") != 0);
    }
#endif

    // Classification of what a pipeline's own cache holds after creation.
    {
      const auto seed = MakeEntry(5, Payload(id, 40000, 3), 40000);
      CHECK(Classify(nullptr, Payload(id, 40000, 3)) == Outcome::kStored);
      CHECK(Classify(&seed, *seed.data) == Outcome::kReused);
      CHECK(Classify(&seed, Payload(id, 40000, 4)) == Outcome::kStored);   // re-serialized
      CHECK(Classify(&seed, Payload(id, 41000, 3)) == Outcome::kStored);   // driver metadata
      CHECK(Classify(&seed, Payload(id, 80000, 3)) == Outcome::kStale);    // added a second copy
    }

    // Size over sessions, in the driver model. 300 pipelines; each session, the driver finds
    // a third of them again under a new key (as on the AMD Windows driver in #167, where a
    // third of the stored pipelines missed the supplied data at every start).
    {
      const size_t pipelines = 300, compiled = 46000;
      auto driver_key = [](size_t pipeline, int session) -> uint64_t {
        return pipeline % 3 == 0 ? (uint64_t(session) << 32) | pipeline : pipeline;
      };
      // One cache seeded with the whole previous file (format 1): it only grows.
      std::vector<uint8_t> single;
      std::vector<size_t> single_sizes;
      for (int session = 0; session < 6; ++session) {
        ModelCache cache = ModelLoad(single.empty() ? nullptr : &single);
        for (size_t p = 0; p < pipelines; ++p) ModelCreate(cache, driver_key(p, session), compiled);
        single = ModelData(id, cache);
        single_sizes.push_back(single.size());
      }
      for (size_t s = 1; s < single_sizes.size(); ++s)
        CHECK(single_sizes[s] > single_sizes[s - 1] + pipelines / 3 * compiled - 1);
      // One entry per pipeline, replaced when stale: the same size at every start.
      std::map<uint64_t, Entry> stored;
      std::vector<uint64_t> file_sizes;
      size_t reused = 0, rebuilt = 0;
      for (int session = 0; session < 6; ++session) {
        std::map<uint64_t, Entry> used;
        for (size_t p = 0; p < pipelines; ++p) {
          const auto it = stored.find(p);
          const Entry* seed = it == stored.end() ? nullptr : &it->second;
          ModelCache cache = ModelLoad(seed ? seed->data.get() : nullptr);
          ModelCreate(cache, driver_key(p, session), compiled);
          auto data = ModelData(id, cache);
          switch (Classify(seed, data)) {
            case Outcome::kReused: used[p] = *seed; ++reused; break;
            case Outcome::kStored: {
              const size_t fresh = seed ? seed->fresh_size : data.size();
              used[p] = MakeEntry(p, std::move(data), fresh);
              break;
            }
            case Outcome::kStale: {
              ModelCache fresh_cache;
              ModelCreate(fresh_cache, driver_key(p, session), compiled);
              auto fresh = ModelData(id, fresh_cache);
              const size_t size = fresh.size();
              used[p] = MakeEntry(p, std::move(fresh), size);
              ++rebuilt;
              break;
            }
          }
        }
        std::vector<Entry> list;
        for (auto& [key, entry] : used) list.push_back(entry);
        const auto saved = Write(path, list, id);
        CHECK(saved.written);
        file_sizes.push_back(saved.bytes);
        stored.clear();
        for (auto& entry : Read(path, id).entries) stored[entry.key] = entry;
        CHECK(stored.size() == pipelines);
      }
      for (size_t s = 1; s < file_sizes.size(); ++s) CHECK(file_sizes[s] == file_sizes[0]);
      CHECK(reused == 5 * (pipelines - pipelines / 3) && rebuilt == 5 * (pipelines / 3));
      std::cout << "Driver model, 6 starts: one seeded cache " << single_sizes.front() << " -> "
                << single_sizes.back() << " bytes; per-pipeline entries " << file_sizes.front()
                << " -> " << file_sizes.back() << " bytes\n";
      // Entries of descriptions no longer stored are dropped by the caller (PruneDriverPipelineCache);
      // stale data never accumulates inside an entry.
      for (const auto& [key, entry] : stored) CHECK(entry.size() <= StaleLimit(entry.fresh_size));
    }

    cleanup();
    std::cout << "Driver cache identity, corruption, size bounds and atomic replacement passed\n";
    return 0;
  } catch (const std::exception& e) {
    cleanup();
    std::cerr << e.what() << '\n'; return 1;
  }
}
