#pragma once
// Reads the I2S0 clock dividers straight from the hardware (used by diagnostics/session_test.yaml): proves which
// clock a session really runs with. IDF's own "Clock division info" is a debug log, compiled out here.
// Expected at 48 kHz, 2 x 32-bit slots: TX clkm_div 13, bck_div 4 (160 MHz source, MCLK 256). RX with
// rx_clock: fast: clkm_div 2, bck_div 39 (PLL 240 MHz / 2); without it RX = TX. rx_timing 0x00000001 with
// rx_data_delay: pos_edge (rx_sd_in_dm = bits 1:0), 0 without. The RX bck_div is the full-duplex slave's MCLK/BCLK
// ratio (docs/AUDIO_INVESTIGATION.md "RX clock margin scan").
#include "esphome/core/log.h"
#include "soc/i2s_struct.h"

namespace i2s_clock {

inline void log_clock() {
  ESP_LOGI("i2s_clock", "I2S0 RX: clkm_div %u, bck_div %u, slave %u, rx_timing 0x%08x | TX: clkm_div %u, bck_div %u",
           (unsigned) I2S0.rx_clkm_conf.rx_clkm_div_num, (unsigned) I2S0.rx_conf1.rx_bck_div_num + 1,
           (unsigned) I2S0.rx_conf.rx_slave_mod, (unsigned) I2S0.rx_timing.val,
           (unsigned) I2S0.tx_clkm_conf.tx_clkm_div_num, (unsigned) I2S0.tx_conf1.tx_bck_div_num + 1);
}

}  // namespace i2s_clock
