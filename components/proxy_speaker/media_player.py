import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import media_player, speaker
from esphome.const import CONF_ID, CONF_TARGET

DEPENDENCIES = ["media_player", "speaker"]

proxy_speaker_ns = cg.esphome_ns.namespace("proxy_speaker")
ProxySpeaker = proxy_speaker_ns.class_("ProxySpeaker", media_player.MediaPlayer, cg.Component)

CONF_SHUTDOWN_DELAY = "shutdown_delay"
CONF_SPEAKER = "speaker"

CONFIG_SCHEMA = media_player.media_player_schema(ProxySpeaker).extend(
    {
        cv.Required(CONF_TARGET): cv.use_id(media_player.MediaPlayer),
        cv.Optional(CONF_SHUTDOWN_DELAY, default="600ms"): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_SPEAKER): cv.use_id(speaker.Speaker),
    }
).extend(cv.COMPONENT_SCHEMA)

async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await media_player.register_media_player(var, config)

    target = await cg.get_variable(config[CONF_TARGET])
    cg.add(var.set_target(target))
    
    cg.add(var.set_shutdown_delay(config[CONF_SHUTDOWN_DELAY]))

    if CONF_SPEAKER in config:
        spk = await cg.get_variable(config[CONF_SPEAKER])
        cg.add(var.set_speaker(spk))
