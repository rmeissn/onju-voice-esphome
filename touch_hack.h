#pragma once
#include "soc/sens_struct.h"

static inline void set_touch_threshold(int id, uint32_t thresh) {
    if (id > 0 && id <= 14) {
        SENS.touch_thresh[id - 1].thresh = thresh;
    }
}
