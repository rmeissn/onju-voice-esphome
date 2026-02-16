#pragma once

#include "esphome.h"
#include "esphome/components/media_player/media_player.h"
#include "esphome/components/speaker/speaker.h"

namespace esphome {
namespace proxy_speaker {

class ProxySpeaker : public media_player::MediaPlayer, public Component {
 public:
  void set_target(media_player::MediaPlayer *target) { this->target_media_player_ = target; }
  void set_shutdown_delay(uint32_t delay_ms) { this->shutdown_delay_ = delay_ms; }
  void set_speaker(speaker::Speaker *speaker) { this->speaker_ = speaker; }

  void setup() override {
    if (this->target_media_player_ != nullptr) {
      this->state = this->target_media_player_->state;
    }
  }

  void loop() override {
    if (this->target_media_player_ == nullptr)
      return;

    auto target_state = this->target_media_player_->state;

    // Special handling for transition to IDLE
    if (target_state == media_player::MEDIA_PLAYER_STATE_IDLE && 
        (this->state == media_player::MEDIA_PLAYER_STATE_PLAYING || this->state == media_player::MEDIA_PLAYER_STATE_ANNOUNCING)) {
      
      if (waiting_for_hardware_) {
         // If speaker is configured, wait for it to stop running
         if (this->speaker_ != nullptr && (this->speaker_->is_running() || this->speaker_->has_buffered_data())) {
             wait_start_time_ = millis(); // Reset timer while speaker is running
             return; 
         }

         // Wait for configured shutdown delay
         if (millis() - wait_start_time_ > this->shutdown_delay_) { 
             waiting_for_hardware_ = false;
             this->state = media_player::MEDIA_PLAYER_STATE_IDLE;
             this->publish_state();
         }
         return; // Still waiting, do not publish IDLE yet
      } else {
         // Start waiting
         waiting_for_hardware_ = true;
         wait_start_time_ = millis();
         return; // Do not publish IDLE yet
      }
    }
    
    // Reset wait flag if we are not in the IDLE transition
    if (target_state != media_player::MEDIA_PLAYER_STATE_IDLE) {
        waiting_for_hardware_ = false;
    }

    // Mirror all other states immediately
    if (this->state != target_state && !waiting_for_hardware_) {
      this->state = target_state;
      this->publish_state();
    }
  }

  media_player::MediaPlayerTraits get_traits() override {
    if (this->target_media_player_ != nullptr)
      return this->target_media_player_->get_traits();
    return media_player::MediaPlayerTraits();
  }

 protected:
  media_player::MediaPlayer *target_media_player_{nullptr};
  speaker::Speaker *speaker_{nullptr};
  uint32_t shutdown_delay_{600}; // Default 600ms
  bool waiting_for_hardware_ = false;
  uint32_t wait_start_time_ = 0;

  void control(const media_player::MediaPlayerCall &call) override {
    if (this->target_media_player_ == nullptr)
      return;

    auto downstream_call = this->target_media_player_->make_call();
    
    if (call.get_media_url().has_value())
      downstream_call.set_media_url(call.get_media_url().value());
      
    if (call.get_command().has_value())
      downstream_call.set_command(call.get_command().value());
      
    if (call.get_volume().has_value())
      downstream_call.set_volume(call.get_volume().value());
      
    if (call.get_announcement().has_value())
      downstream_call.set_announcement(call.get_announcement().value());

    downstream_call.perform();
  }
};

} // namespace proxy_speaker
} // namespace esphome
