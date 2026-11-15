#pragma once
// Periodic resource monitor for onju_duplex_i2s.yaml (called from `interval:` lambdas).
//
// log_stats()  - cheap, every 10s: used/free/minimum free of internal RAM, DMA-capable RAM and PSRAM (cached heap
//                counters, no heap walk) and the CPU load per core (100% - idle task share).
// log_details() - expensive, OPTIONAL (interval commented out in the yaml): largest free block per heap
//                (fragmentation), busiest tasks of the last interval and the tasks with the least stack headroom.
//                Logs its own duration ("took").
//
// Why log_details() is expensive (checked in the ESP-IDF 5.5.6 source):
//   - heap_caps_get_largest_free_block()/heap_caps_get_info() walk every block of a heap while holding the heap
//     spinlock (portENTER_CRITICAL: interrupts masked on this core, other core waits on allocations from that heap).
//   - uxTaskGetSystemState() holds the kernel spinlock and scans the unused stack of EVERY task byte by byte for the
//     high water mark (also stacks in PSRAM). The other core's scheduler/ISRs wait for that lock.
//   Both can delay the audio task by a few ms, so only run it rarely (e.g. every 60s) and check the "took" values.
//
// log_heap_owners() - DIAGNOSTIC builds only (needs CONFIG_HEAP_TASK_TRACKING, which adds a header to every heap
//                block): who owns the heap. Per task the internal RAM and PSRAM it allocated, the largest internal
//                blocks and a size histogram of the internal blocks (shows e.g. how much the <= 4kB blocks cost that
//                CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL keeps internal). ESP-IDF credits a block to the task that
//                allocated it: everything created in the components' setup() and task stacks created from there
//                count for the ESPHome loop task. Expensive (heap walk + task snapshot), log line shows "took".
//
// Logs under tag "monitor", level INFO (WARN if a task stack headroom is below STACK_WARN_BYTES).
// Needs these sdkconfig options (set in onju_duplex_i2s.yaml):
//   CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS (+ USE_TRACE_FACILITY), CONFIG_FREERTOS_RUN_TIME_COUNTER_TYPE_U64
// Optional for log_details() (task core affinity, otherwise shown as '?'):
//   CONFIG_FREERTOS_USE_STATS_FORMATTING_FUNCTIONS + CONFIG_FREERTOS_VTASKLIST_INCLUDE_COREID
// The run time clock is esp_timer (1 us). Run time is only credited to a task on a context switch, so the load of an
// interval is a close approximation, not an exact value.

#include <algorithm>
#include <cinttypes>
#include <cstdio>

#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <sdkconfig.h>
#ifdef CONFIG_HEAP_TASK_TRACKING
#include <esp_heap_task_info.h>
#include <esp_memory_utils.h>
#endif

#include "esphome/core/log.h"

namespace onju_monitor {

static const char *const TAG = "monitor";
static constexpr size_t MAX_TASKS = 64;           // the duplex config runs ~35-45 tasks
static constexpr size_t TOP_N = 6;                // tasks per line (busiest / least stack)
static constexpr uint32_t STACK_WARN_BYTES = 512;  // warn below this stack headroom

inline float kb(size_t bytes) { return bytes / 1024.0f; }

// ---- cheap part (every 10s) ----

inline void log_heap(const char *name, uint32_t caps) {
  // total_size includes heap metadata, so "used" is slightly higher than the sum of all allocations
  const size_t total = heap_caps_get_total_size(caps);
  const size_t free = heap_caps_get_free_size(caps);
  ESP_LOGI(TAG, "%s: used=%.1fkB free=%.1fkB min=%.1fkB", name, kb(total - free), kb(free),
           kb(heap_caps_get_minimum_free_size(caps)));
}

inline void log_cpu() {
  static uint64_t prev_wall = 0;
  static uint64_t prev_idle[2] = {0, 0};
  const uint64_t wall = esp_timer_get_time();
  const uint64_t idle[2] = {ulTaskGetIdleRunTimeCounterForCore(0), ulTaskGetIdleRunTimeCounterForCore(1)};
  if (prev_wall != 0 && wall > prev_wall) {
    const float dt = wall - prev_wall;
    float load[2];
    for (int c = 0; c < 2; c++)
      load[c] = 100.0f - std::min(100.0f, 100.0f * (idle[c] - prev_idle[c]) / dt);
    ESP_LOGI(TAG, "CPU: core0=%.1f%% core1=%.1f%% (last %.1fs)", load[0], load[1], dt / 1e6f);
  }
  prev_wall = wall;
  prev_idle[0] = idle[0];
  prev_idle[1] = idle[1];
}

inline void log_stats() {
  log_heap("RAM internal", MALLOC_CAP_INTERNAL);
  log_heap("RAM DMA", MALLOC_CAP_DMA);
  log_heap("PSRAM", MALLOC_CAP_SPIRAM);
  log_cpu();
}

// ---- expensive part (every 60s) ----

struct TaskSample {
  TaskHandle_t handle;
  uint64_t runtime;
};

// Kept in PSRAM: internal RAM is the bottleneck of the duplex config.
struct State {
  TaskStatus_t status[MAX_TASKS];
  TaskSample prev[MAX_TASKS];
  uint64_t delta[MAX_TASKS];
  uint8_t order[MAX_TASKS];
  size_t prev_count;
  uint64_t prev_total;
};

#if (configTASKLIST_INCLUDE_COREID == 1)
inline char core_char(const TaskStatus_t &t) { return t.xCoreID == 0 ? '0' : (t.xCoreID == 1 ? '1' : '*'); }
#else
inline char core_char(const TaskStatus_t &) { return '?'; }  // CONFIG_FREERTOS_VTASKLIST_INCLUDE_COREID not set
#endif

inline void log_details() {
  static State *s = nullptr;
  if (s == nullptr) {
    s = static_cast<State *>(heap_caps_calloc(1, sizeof(State), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (s == nullptr) {
      ESP_LOGW(TAG, "No memory for task statistics");
      return;
    }
  }

  // Largest free blocks (heap walk)
  const int64_t t0 = esp_timer_get_time();
  const size_t largest_int = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL);
  const size_t largest_dma = heap_caps_get_largest_free_block(MALLOC_CAP_DMA);
  const size_t largest_ps = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
  const int64_t t1 = esp_timer_get_time();
  ESP_LOGI(TAG, "Largest free block: internal=%.1fkB DMA=%.1fkB PSRAM=%.0fkB (took %" PRId64 "us)", kb(largest_int),
           kb(largest_dma), kb(largest_ps), t1 - t0);

  // Task snapshot (kernel lock + stack scan)
  configRUN_TIME_COUNTER_TYPE total = 0;
  const int64_t t2 = esp_timer_get_time();
  const UBaseType_t n = uxTaskGetSystemState(s->status, MAX_TASKS, &total);
  const int64_t took = esp_timer_get_time() - t2;
  if (n == 0) {
    ESP_LOGW(TAG, "More than %u tasks, increase MAX_TASKS in onju_monitor.h", (unsigned) MAX_TASKS);
    return;
  }

  const bool have_prev = s->prev_total != 0;
  const uint64_t dt = total - s->prev_total;  // elapsed time in us (per core)
  const TaskHandle_t idle[2] = {xTaskGetIdleTaskHandleForCore(0), xTaskGetIdleTaskHandleForCore(1)};

  for (UBaseType_t i = 0; i < n; i++) {
    const TaskStatus_t &t = s->status[i];
    uint64_t prev = 0;
    for (size_t j = 0; j < s->prev_count; j++) {
      if (s->prev[j].handle == t.xHandle) {
        prev = s->prev[j].runtime;
        break;
      }
    }
    // new task (or reused handle): count its whole run time
    s->delta[i] = t.ulRunTimeCounter >= prev ? t.ulRunTimeCounter - prev : t.ulRunTimeCounter;
    s->order[i] = i;
  }
  for (UBaseType_t i = 0; i < n; i++)
    s->prev[i] = {s->status[i].xHandle, s->status[i].ulRunTimeCounter};
  s->prev_count = n;
  s->prev_total = total;

  char buf[320] = "";
  int pos;

  // Busiest tasks (percent of one core, so the sum over both cores can reach 200%)
  if (have_prev && dt > 0) {
    std::sort(s->order, s->order + n, [&](uint8_t a, uint8_t b) { return s->delta[a] > s->delta[b]; });
    pos = 0;
    size_t shown = 0;
    for (UBaseType_t k = 0; k < n && shown < TOP_N; k++) {
      const TaskStatus_t &t = s->status[s->order[k]];
      if (t.xHandle == idle[0] || t.xHandle == idle[1])
        continue;
      pos += snprintf(buf + pos, sizeof(buf) - pos, " %s(%c)=%.1f%%", t.pcTaskName, core_char(t),
                      100.0f * s->delta[s->order[k]] / dt);
      if (pos >= (int) sizeof(buf))
        break;
      shown++;
    }
    ESP_LOGI(TAG, "Busiest tasks (last %.0fs):%s", dt / 1e6f, buf);
  }

  // Least stack headroom since task start (ESP-IDF: high water mark is in bytes)
  std::sort(s->order, s->order + n, [&](uint8_t a, uint8_t b) {
    return s->status[a].usStackHighWaterMark < s->status[b].usStackHighWaterMark;
  });
  buf[0] = '\0';
  pos = 0;
  bool low = false;
  for (size_t k = 0; k < n && k < TOP_N; k++) {
    const TaskStatus_t &t = s->status[s->order[k]];
    low |= t.usStackHighWaterMark < STACK_WARN_BYTES;
    pos += snprintf(buf + pos, sizeof(buf) - pos, " %s(%c)=%u", t.pcTaskName, core_char(t),
                    (unsigned) t.usStackHighWaterMark);
    if (pos >= (int) sizeof(buf))
      break;
  }
  if (low) {
    ESP_LOGW(TAG, "Tasks=%u (snapshot took %" PRId64 "us) | stack headroom min [B]:%s", (unsigned) n, took, buf);
  } else {
    ESP_LOGI(TAG, "Tasks=%u (snapshot took %" PRId64 "us) | stack headroom min [B]:%s", (unsigned) n, took, buf);
  }
}

// ---- diagnostic part (RAM test builds with CONFIG_HEAP_TASK_TRACKING) ----

#ifdef CONFIG_HEAP_TASK_TRACKING
static constexpr size_t MAX_OWNERS = 64;
static constexpr size_t MAX_BLOCKS = 4000;  // all heaps; more blocks are not reported (warning)
static constexpr size_t TOP_BLOCKS = 20;
static constexpr size_t TOP_OWNERS = 14;

struct HeapDiag {  // in PSRAM (~60kB), allocated on first use
  heap_task_totals_t totals[MAX_OWNERS];
  heap_task_block_t blocks[MAX_BLOCKS];
  TaskStatus_t status[MAX_TASKS];
};

inline const char *owner_name(TaskHandle_t h, const TaskStatus_t *st, UBaseType_t n) {
  if (h == nullptr)
    return "(no task: startup/ISR)";
  for (UBaseType_t i = 0; i < n; i++)
    if (st[i].xHandle == h)
      return st[i].pcTaskName;
  return "(deleted task)";  // never dereference the handle of a task that no longer exists
}

inline void log_heap_owners() {
  static HeapDiag *d = nullptr;
  if (d == nullptr) {
    d = static_cast<HeapDiag *>(heap_caps_calloc(1, sizeof(HeapDiag), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (d == nullptr) {
      ESP_LOGW(TAG, "No memory for the heap owner statistics");
      return;
    }
  }
  const int64_t t0 = esp_timer_get_time();
  configRUN_TIME_COUNTER_TYPE total = 0;
  const UBaseType_t nt = uxTaskGetSystemState(d->status, MAX_TASKS, &total);
  // ESP-IDF adds onto entries already in the totals array (meant for comparing consecutive calls): zero it, otherwise
  // the per-owner sums accumulate over all calls (seen 2026-11-14: "tracked internal" grew beyond the heap size).
  std::fill(d->totals, d->totals + MAX_OWNERS, heap_task_totals_t{});
  size_t num_totals = 0;
  heap_task_info_params_t p = {};
  p.caps[0] = MALLOC_CAP_SPIRAM;  // index 0: PSRAM regions
  p.mask[0] = MALLOC_CAP_SPIRAM;
  p.caps[1] = 0;  // index 1: all other regions = internal RAM
  p.mask[1] = 0;
  p.totals = d->totals;
  p.num_totals = &num_totals;
  p.max_totals = MAX_OWNERS;
  p.blocks = d->blocks;
  p.max_blocks = MAX_BLOCKS;
  const size_t nb = heap_caps_get_per_task_info(&p);
  const int64_t took = esp_timer_get_time() - t0;

  // per owner: internal and PSRAM totals, sorted by internal
  size_t sum_int = 0, sum_ps = 0;
  for (size_t i = 0; i < num_totals; i++) {
    sum_int += d->totals[i].size[1];
    sum_ps += d->totals[i].size[0];
  }
  std::sort(d->totals, d->totals + num_totals,
            [](const heap_task_totals_t &a, const heap_task_totals_t &b) { return a.size[1] > b.size[1]; });
  ESP_LOGI(TAG, "Heap owners (took %" PRId64 "us): tracked internal=%.1fkB PSRAM=%.1fkB, %u owners, %u blocks%s", took,
           kb(sum_int), kb(sum_ps), (unsigned) num_totals, (unsigned) nb,
           nb >= MAX_BLOCKS ? " (block list TRUNCATED, increase MAX_BLOCKS)" : "");
  for (size_t i = 0; i < num_totals && i < TOP_OWNERS; i++) {
    const heap_task_totals_t &t = d->totals[i];
    ESP_LOGI(TAG, "  owner %-16s internal=%6.1fkB (%3u blocks) PSRAM=%7.1fkB (%3u blocks)",
             owner_name(t.task, d->status, nt), kb(t.size[1]), (unsigned) t.count[1], kb(t.size[0]),
             (unsigned) t.count[0]);
  }

  // internal blocks only: size histogram + largest blocks
  static constexpr size_t LIMITS[] = {64, 256, 1024, 4096, SIZE_MAX};
  static constexpr const char *LABELS[] = {"<=64B", "<=256B", "<=1kB", "<=4kB", ">4kB"};
  size_t hist_n[5] = {}, hist_sum[5] = {};
  size_t ni = 0;
  for (size_t i = 0; i < nb; i++) {
    if (!esp_ptr_internal(d->blocks[i].address))
      continue;
    d->blocks[ni++] = d->blocks[i];  // compact the internal blocks to the front
    const size_t sz = d->blocks[ni - 1].size;
    for (size_t b = 0; b < 5; b++) {
      if (sz <= LIMITS[b]) {
        hist_n[b]++;
        hist_sum[b] += sz;
        break;
      }
    }
  }
  char buf[320] = "";
  int pos = 0;
  for (size_t b = 0; b < 5; b++)
    pos += snprintf(buf + pos, sizeof(buf) - pos, " %s: %u=%.1fkB", LABELS[b], (unsigned) hist_n[b], kb(hist_sum[b]));
  ESP_LOGI(TAG, "  internal blocks by size:%s", buf);
  std::sort(d->blocks, d->blocks + ni,
            [](const heap_task_block_t &a, const heap_task_block_t &b) { return a.size > b.size; });
  for (size_t i = 0; i < ni && i < TOP_BLOCKS; i++) {
    ESP_LOGI(TAG, "  internal block %2u: %6u B at %p by %s", (unsigned) i + 1, (unsigned) d->blocks[i].size,
             d->blocks[i].address, owner_name(d->blocks[i].task, d->status, nt));
  }
}
#else
inline void log_heap_owners() { ESP_LOGW(TAG, "log_heap_owners() needs CONFIG_HEAP_TASK_TRACKING (diagnostic build)"); }
#endif

}  // namespace onju_monitor
