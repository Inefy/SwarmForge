"""
Battencruiser v2 - adaptive Terran bot for the aiarena.net ladder.

Strategy layer (persistent, per opponent, race-tuned defaults):
  * three_rax   - 3-barracks marine/stim timing, transitions into macro play
  * bio_macro   - 1-rax expand into multi-base bio + medivacs + tanks + upgrades
  * proxy_2rax  - proxy barracks marine all-in with SCV pull

Combat engine:
  * range-aware stutter-step micro (kite melee & shorter-ranged, close on longer-ranged)
  * target priority (banelings/casters/siege > attackers > workers > rest, lowest HP first)
  * dodges psi storm, corrosive bile, nukes, lurker spikes, liberator zones, banelings
  * fight-or-flee: local power evaluation, disengages losing fights, regroups, re-engages
  * reinforcement grouping so units don't trickle into the enemy one by one
  * medivac boost-follow + retreat, tank siege management, viking response to air comps

Macro engine: ramp wall + depot raise/lower + SCV repair, bunker vs rushes,
orbital MULEs/scans, planetary fortresses on far bases, missile turrets,
rush / worker-rush / cannon-rush defense, adaptive frame skip under load.
"""

from __future__ import annotations

from sc2.bot_ai import BotAI
from sc2.data import Result
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.effect_id import EffectId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2

from bot.strategy import StrategyManager
from bot.recorder import GameRecorder

DEPOT_TYPES = {UnitTypeId.SUPPLYDEPOT, UnitTypeId.SUPPLYDEPOTLOWERED, UnitTypeId.SUPPLYDEPOTDROP}
BIO_TYPES = {UnitTypeId.MARINE, UnitTypeId.MARAUDER, UnitTypeId.REAPER, UnitTypeId.GHOST}
TANK_TYPES = {UnitTypeId.SIEGETANK, UnitTypeId.SIEGETANKSIEGED}
VIKING_TYPES = {UnitTypeId.VIKINGFIGHTER, UnitTypeId.VIKINGASSAULT}
MECH_TYPES = TANK_TYPES | {
    UnitTypeId.HELLION, UnitTypeId.HELLIONTANK, UnitTypeId.WIDOWMINE,
    UnitTypeId.WIDOWMINEBURROWED, UnitTypeId.CYCLONE, UnitTypeId.THOR,
}
AIR_TYPES = VIKING_TYPES | {
    UnitTypeId.MEDIVAC, UnitTypeId.LIBERATOR, UnitTypeId.LIBERATORAG,
    UnitTypeId.RAVEN, UnitTypeId.BANSHEE, UnitTypeId.BATTLECRUISER,
}
ARMY_TYPES = BIO_TYPES | MECH_TYPES | AIR_TYPES
WORKER_TYPES = {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE, UnitTypeId.MULE}
IGNORE_TARGETS = {UnitTypeId.LARVA, UnitTypeId.EGG, UnitTypeId.BROODLING, UnitTypeId.INTERCEPTOR}
SCOUT_IGNORE = {UnitTypeId.OVERLORD, UnitTypeId.OVERSEER, UnitTypeId.OBSERVER}
SCOUT_PROXY_STRUCTS = {
    UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT,
    UnitTypeId.GATEWAY, UnitTypeId.WARPGATE, UnitTypeId.ROBOTICSFACILITY,
    UnitTypeId.FORGE, UnitTypeId.PYLON, UnitTypeId.PHOTONCANNON,
    UnitTypeId.BUNKER, UnitTypeId.SPAWNINGPOOL,
}
ENEMY_TOWNHALLS = {
    UnitTypeId.HATCHERY, UnitTypeId.LAIR, UnitTypeId.HIVE, UnitTypeId.NEXUS,
    UnitTypeId.COMMANDCENTER, UnitTypeId.ORBITALCOMMAND, UnitTypeId.PLANETARYFORTRESS,
}
STIM_ABILITY = {
    UnitTypeId.MARINE: AbilityId.EFFECT_STIM_MARINE,
    UnitTypeId.MARAUDER: AbilityId.EFFECT_STIM_MARAUDER,
}
HARASSER_TYPES = {
    UnitTypeId.REAPER, UnitTypeId.HELLION, UnitTypeId.HELLIONTANK,
    UnitTypeId.ORACLE, UnitTypeId.PHOENIX, UnitTypeId.MUTALISK,
    UnitTypeId.BANSHEE, UnitTypeId.DARKTEMPLAR, UnitTypeId.VOIDRAY,
    UnitTypeId.ADEPT,
}
DEFENSIVE_STRUCTS = {
    UnitTypeId.PHOTONCANNON, UnitTypeId.BUNKER, UnitTypeId.SPINECRAWLER,
    UnitTypeId.SPORECRAWLER, UnitTypeId.MISSILETURRET, UnitTypeId.SHIELDBATTERY,
    UnitTypeId.PLANETARYFORTRESS, UnitTypeId.PYLON,
}
CLOAK_UNIT_HINTS = {
    UnitTypeId.BANSHEE, UnitTypeId.DARKTEMPLAR, UnitTypeId.GHOST,
    UnitTypeId.LURKERMP, UnitTypeId.LURKERMPBURROWED, UnitTypeId.MOTHERSHIP,
}
CLOAK_STRUCT_HINTS = {UnitTypeId.DARKSHRINE, UnitTypeId.GHOSTACADEMY, UnitTypeId.LURKERDENMP}

# Kill these first: splash / spellcasters / dug-in siege units.
SPECIAL_TARGETS = {
    UnitTypeId.BANELING, UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED,
    UnitTypeId.INFESTOR, UnitTypeId.HIGHTEMPLAR, UnitTypeId.DISRUPTOR,
    UnitTypeId.ORACLE, UnitTypeId.LURKERMPBURROWED, UnitTypeId.SIEGETANKSIEGED,
    UnitTypeId.RAVAGER, UnitTypeId.VIPER,
}
# Enemy air comps that trigger viking production.
AIR_THREAT_TYPES = {
    UnitTypeId.BATTLECRUISER, UnitTypeId.CARRIER, UnitTypeId.VOIDRAY,
    UnitTypeId.MUTALISK, UnitTypeId.PHOENIX, UnitTypeId.TEMPEST,
    UnitTypeId.BROODLORD, UnitTypeId.LIBERATOR, UnitTypeId.LIBERATORAG,
    UnitTypeId.BANSHEE, UnitTypeId.CORRUPTOR,
}
# Spellcasters have 0 dps but huge impact - nominal dps for power evaluation.
CASTER_NOMINAL_DPS = {
    UnitTypeId.INFESTOR: 14, UnitTypeId.HIGHTEMPLAR: 14, UnitTypeId.DISRUPTOR: 14,
    UnitTypeId.VIPER: 12, UnitTypeId.ORACLE: 15, UnitTypeId.RAVEN: 8,
    UnitTypeId.WIDOWMINE: 12, UnitTypeId.WIDOWMINEBURROWED: 16,
    UnitTypeId.BANELING: 16, UnitTypeId.LURKERMP: 10,
}
STATIC_DEFENSE_DPS = {
    UnitTypeId.PHOTONCANNON: 22, UnitTypeId.SPINECRAWLER: 22,
    UnitTypeId.SPORECRAWLER: 18, UnitTypeId.BUNKER: 28,
    UnitTypeId.PLANETARYFORTRESS: 35, UnitTypeId.MISSILETURRET: 25,
}
# Ground AoE / zones to walk out of. Margin added to the effect radius.
DODGE_EFFECTS = {
    EffectId.PSISTORMPERSISTENT: 1.5,
    EffectId.RAVAGERCORROSIVEBILECP: 1.2,
    EffectId.NUKEPERSISTENT: 2.0,
    EffectId.LURKERMP: 1.0,
    EffectId.BLINDINGCLOUDCP: 1.0,
    EffectId.LIBERATORTARGETMORPHDELAYPERSISTENT: 1.0,
    EffectId.LIBERATORTARGETMORPHPERSISTENT: 1.0,
}


class BattencruiserBot(BotAI):
    NAME = "Battencruiser"
    RACE_NAME = "Terran"

    def __init__(self):
        self.raw_affects_selection = True
        self.strategy = None
        self.attack_mode = False
        self.scv_pulled_for_allin = False
        self.enemy_rush_detected = False
        self.worker_rush_active = False
        self.cloak_threat = False
        self.scout_sent = False
        self.scout_tag = None
        self.proxy_scv_tags = []
        self.proxy_point = None
        self.natural_position = None
        self.enemy_natural = None
        self.staging_point = None
        self.greeted = False
        self.enemy_main_visited = False
        self.enemy_natural_visited = False
        self.cannon_targets = None
        self._base_threats = None
        self._scan_request = None
        self._retreat_until = 0.0
        self._max_air_threat = 0
        self._bunker_last_threat = 0.0
        self._bunker_assignees = set()
        self._rush_seen_live = False
        self._cloak_seen_live = False
        self._air_seen_live = 0
        self._armored_seen_max = 0
        self._light_seen_max = 0
        self._heavy_air_seen = 0
        self._splash_threat = False
        self._enemy_base_count = 0
        self._proxy_alert = False
        self._all_in_suspected = False
        self._enemy_tech_seen = False
        self._first_pressure_recorded = False
        self._cached_bio_for_spread = None
        self._point_order_cache = {}
        self._special_last_cast = {}
        self._reported_errors = set()
        self._stats = {}
        self._retreat_count = 0
        self._focus_board = {}
        self._recorder = GameRecorder('Battencruiser', 'battencruiser')
        self._tank_last_enemy = {}
        self._wall_positions = []
        self._rax_wall_position = None
        self._stim_ready = False
        self._cached_enemies = None
        self._cached_banelings = None
        self._cached_air_enemies = None
        self._dodge_zones = []

    # ------------------------------------------------------------------ setup

    async def on_start(self):
        try:
            self.client.game_step = 2
        except Exception:
            pass
        try:
            race_name = getattr(getattr(self, "enemy_race", None), "name", "Random")
        except Exception:
            race_name = "Random"
        try:
            try:
                map_name = self.game_info.map_name
            except Exception:
                map_name = None
            self.strategy = StrategyManager(getattr(self, "opponent_id", None), race_name, map_name)
        except Exception:
            self.strategy = None

        # Pre-adapt using what earlier games taught us about this opponent.
        try:
            if self.strategy:
                if self.strategy.expects("rushed") or self.strategy.expects("worker_rush"):
                    self.enemy_rush_detected = True  # bunker up, hold the wall, delay expand
                try:
                    fp = float(self.strategy.profile.get("first_pressure_ewma", 9999))
                    samples = int(self.strategy.profile.get("pressure_samples", 0))
                    if samples >= 3 and fp < 360:
                        self.enemy_rush_detected = True  # their pressure comes early
                except Exception:
                    pass
                if self.strategy.expects("cloak"):
                    self.cloak_threat = True  # ebay + turrets + scan energy early
                if self.strategy.expects("air"):
                    self._max_air_threat = max(3, self.strategy.expected_max_air())
        except Exception:
            pass

        enemy_start = self.enemy_start_locations[0]
        try:
            expos = sorted(self.expansion_locations_list, key=lambda p: p.distance_to(self.start_location))
            self.natural_position = expos[1] if len(expos) > 1 else self.start_location
            expos_e = sorted(self.expansion_locations_list, key=lambda p: p.distance_to(enemy_start))
            self.enemy_natural = expos_e[1] if len(expos_e) > 1 else enemy_start
        except Exception:
            self.natural_position = self.start_location
            self.enemy_natural = enemy_start

        self.staging_point = self.natural_position.towards(self.game_info.map_center, 6)
        center = self.game_info.map_center
        self.proxy_point = center.towards(enemy_start, center.distance_to(enemy_start) * 0.45)

        try:
            self._wall_positions = list(self.main_base_ramp.corner_depots)
            self._rax_wall_position = self.main_base_ramp.barracks_correct_placement
        except Exception:
            self._wall_positions = []
            self._rax_wall_position = None

    @property
    def _proxying(self):
        return (
            bool(self.strategy)
            and getattr(self.strategy, "location", "home") == "proxy"
            and self.time < 420
        )

    @property
    def active_build(self):
        # Synthetic label: openings are now composed from learned parameters.
        return "proxy_2rax" if self._proxying else "macro"

    @property
    def all_in(self):
        return self._proxying or self.supply_used > 190 or not self.townhalls

    def build_config(self):
        cfg = self._base_build_config()
        # Learned attack timing for this opponent (early / standard / late).
        try:
            if self.strategy:
                cfg["attack_min_bio"] = max(
                    2, int(round(cfg["attack_min_bio"] * self.strategy.aggression_mult))
                )
                cfg["worker_cap"] = max(12, int(round(cfg["worker_cap"] * self.strategy.greed_worker_mult)))
        except Exception:
            pass
        # Known cloak/air opponents: detection and anti-air much earlier.
        if (self.cloak_threat or self._max_air_threat >= 1) and self.active_build != "proxy_2rax":
            cfg["want_ebay"] = cfg["want_ebay"] or self.time > 150
            cfg["want_turrets"] = True
        # Learned tech focus.
        try:
            tech = self.strategy.tech if self.strategy else "tank_bio"
            if self.active_build != "proxy_2rax":
                if tech == "marine_bio":
                    cfg["tank_cap"] = 0
                    cfg["rax_cap"] = cfg["rax_cap"] + 1
                elif tech == "tank_bio":
                    if cfg["tank_cap"] > 0:
                        cfg["tank_cap"] = 4
                elif tech == "marauder_bio":
                    cfg["techlab_cap"] = max(cfg["techlab_cap"], 2)
                    cfg["marauder"] = True
                army = getattr(self.strategy, "army", "mixed")
                if army == "bio":
                    cfg["rax_cap"] += 2
                    cfg["want_ghost_academy"] = self.time > 420
                elif army == "mech":
                    cfg["factory_cap"] = 3
                    cfg["starport_cap"] = max(1, cfg["starport_cap"])
                    cfg["want_factory"] = self.time > 170
                    cfg["want_armory"] = self.time > 300
                    cfg["tank_cap"] = 10
                    cfg["gas_target"] = max(cfg["gas_target"], 5)
                elif army == "sky":
                    cfg["starport_cap"] = 3
                    cfg["factory_cap"] = max(1, cfg["factory_cap"])
                    cfg["want_factory"] = self.time > 170
                    cfg["want_starport"] = self.time > 220
                    cfg["want_fusion"] = self.time > 480
                    cfg["gas_target"] = max(cfg["gas_target"], 6)
                    cfg["medivac_cap"] = 2
                else:
                    cfg["factory_cap"] = max(2, cfg["factory_cap"])
                    cfg["starport_cap"] = max(2, cfg["starport_cap"])
                    cfg["want_factory"] = self.time > 190
                    cfg["want_starport"] = self.time > 240
                    cfg["want_armory"] = self.time > 360
                    cfg["want_ghost_academy"] = self.time > 500
                    cfg["want_fusion"] = self.time > 600
                    cfg["gas_target"] = max(cfg["gas_target"], 5)
        except Exception:
            pass
        return cfg

    def _base_build_config(self):
        t = self.time
        s = self.strategy
        prod = {"1rax": 1, "2rax": 2, "3rax": 3}.get(getattr(s, "production", "2rax"), 2)
        gas0 = {"no_gas": 0, "one_gas": 1, "two_gas": 2}.get(getattr(s, "gas_open", "one_gas"), 1)
        bases = max(1, self.townhalls.amount)
        if self._proxying:
            return dict(
                worker_cap=16, gas_target=0, rax_cap=0, want_factory=False,
                want_starport=False, want_ebay=False, want_turrets=False,
                techlab_cap=0, attack_min_bio=4, retreat_bio=0,
                medivac_cap=0, tank_cap=0, marauder=False, want_bunker=False,
                factory_cap=0, starport_cap=0, want_armory=False,
                want_ghost_academy=False, want_fusion=False,
            )
        developed = bases >= 2 or t > 330
        if not developed:
            rax_cap = prod
            worker_cap = 19 + 2 * prod
            gas_target = gas0 if t < 150 else max(gas0, 1)
        else:
            rax_cap = min(9, 2 * bases + (1 if prod >= 3 else 0))
            worker_cap = min(70, 22 * bases + 2)
            gas_target = 2 if bases < 3 else 4
        return dict(
            worker_cap=worker_cap,
            gas_target=gas_target,
            rax_cap=rax_cap,
            want_factory=(developed and t > 240),
            want_starport=(developed and t > 270),
            want_ebay=(developed and t > 260) or t > 400,
            want_turrets=(t > 320 or self.cloak_threat),
            techlab_cap=(2 if bases >= 3 else 1),
            attack_min_bio=6 + 5 * prod,
            retreat_bio=3 + 2 * prod,
            medivac_cap=((2 if bases < 3 else 4) if developed else 0),
            tank_cap=(2 if bases >= 2 else 0),
            marauder=True,
            want_bunker=self.enemy_rush_detected,
            factory_cap=(1 if developed else 0),
            starport_cap=(1 if developed else 0),
            want_armory=(developed and t > 420),
            want_ghost_academy=False,
            want_fusion=False,
        )

    # ------------------------------------------------------------------ frame

    async def on_step(self, iteration: int):
        try:
            await self._step(iteration)
        except Exception as exc:
            # Never crash out of a ladder game.
            self._report_error("on_step", exc)

    async def _step(self, iteration: int):
        if not self.greeted and iteration >= 2:
            self.greeted = True
            try:
                await self.chat_send("(glhf)")
            except Exception:
                pass

        # Adapt frame skip if we are running out of real-time budget.
        if iteration % 64 == 0 and iteration > 0:
            try:
                avg_ms = self.step_time[1]
                if self.supply_used > 170:
                    self.client.game_step = 6
                elif self.supply_used > 110:
                    self.client.game_step = 4
                elif avg_ms > 66 and self.client.game_step < 4:
                    self.client.game_step = 4
                elif avg_ms < 25 and self.client.game_step > 2:
                    self.client.game_step = 2
                # Hard failsafe: never let a slow step risk an aiarena timeout.
                if avg_ms > 150:
                    self.client.game_step = max(self.client.game_step, 8)
            except Exception:
                pass

        self._update_intel()

        # No bases left: throw everything at the enemy.
        if not self.townhalls:
            await self._safe(self._desperado(iteration))
            return

        cfg = self.build_config()

        await self._safe(self.manage_supply(cfg))
        await self._safe(self.manage_workers(cfg))
        await self._safe(self.manage_orbitals())
        await self._safe(self.manage_gas(cfg))
        await self._safe(self.manage_expansion(cfg))
        await self._safe(self.manage_production(cfg))
        await self._safe(self.manage_proxy())
        await self._safe(self.manage_addons(cfg))
        await self._safe(self.manage_upgrades(cfg))
        await self._safe(self.train_army(cfg))
        await self._safe(self.manage_scout())
        await self._safe(self.manage_defense())
        await self._safe(self.manage_worker_safety())
        if iteration % 2 == 0:
            await self._safe(self.control_army(cfg))
        await self._safe(self.manage_depot_wall())

        if iteration % 16 == 0:
            self._track_stats()
            self._recorder.maybe_snapshot(self)
            await self._safe(self.distribute_workers())

    async def _safe(self, coro):
        try:
            await coro
        except Exception as exc:
            name = getattr(getattr(coro, "cr_code", None), "co_name", "task")
            self._report_error(name, exc)

    def _report_error(self, context, exc):
        key = (context, type(exc).__name__, str(exc))
        if key in self._reported_errors:
            return
        self._reported_errors.add(key)
        print("[%s] %s failed: %s: %s" % (self.NAME, context, type(exc).__name__, exc), flush=True)

    async def on_end(self, game_result):
        try:
            if self.strategy:
                try:
                    self._stats["retreats"] = self._retreat_count
                except Exception:
                    pass
                won = game_result == Result.Victory
                self.strategy.report(won, self._stats)
                try:
                    self._recorder.finish(self, won, getattr(self.strategy, "choices", {}))
                except Exception:
                    pass
        except Exception:
            pass

    def _track_stats(self):
        try:
            s = self._stats
            s["peak_army_supply"] = max(s.get("peak_army_supply", 0), int(self.supply_army))
            s["peak_workers"] = max(s.get("peak_workers", 0), int(self.supply_workers))
            s["max_bases"] = max(s.get("max_bases", 0), self.townhalls.amount)
            s["end_time"] = round(self.time, 1)
        except Exception:
            pass

    # ------------------------------------------------------------------ intel

    def _update_intel(self):
        t = self.time
        enemies = self.enemy_units

        if t < 300:
            aggressors = enemies.filter(
                lambda u: u.type_id not in WORKER_TYPES
                and u.type_id not in SCOUT_IGNORE
                and u.type_id not in IGNORE_TARGETS
                and u.distance_to(self.start_location) < 50
            )
            if aggressors.amount >= 2:
                self.enemy_rush_detected = self._rush_seen_live = True
            if self.enemy_structures.filter(lambda s: s.distance_to(self.start_location) < 45):
                self.enemy_rush_detected = self._rush_seen_live = True

        if t < 180:
            pools = self.enemy_structures(UnitTypeId.SPAWNINGPOOL)
            enemy_bases = self.enemy_structures.of_type(ENEMY_TOWNHALLS)
            if pools and enemy_bases.amount <= 1 and t < 110:
                self.enemy_rush_detected = self._rush_seen_live = True
            gateways = self.enemy_structures.of_type({UnitTypeId.GATEWAY, UnitTypeId.WARPGATE})
            if gateways.amount >= 2 and enemy_bases.amount <= 1:
                self.enemy_rush_detected = self._rush_seen_live = True
            enemy_rax = self.enemy_structures(UnitTypeId.BARRACKS)
            if enemy_rax.amount >= 2 and enemy_bases.amount <= 1:
                self.enemy_rush_detected = self._rush_seen_live = True

        workers_close = enemies.filter(
            lambda u: u.type_id in WORKER_TYPES and u.distance_to(self.start_location) < 22
        )
        self.worker_rush_active = t < 360 and workers_close.amount >= 5

        self.cannon_targets = self.enemy_structures.filter(
            lambda s: s.type_id in {
                UnitTypeId.PYLON, UnitTypeId.PHOTONCANNON, UnitTypeId.FORGE, UnitTypeId.BUNKER
            }
            and s.distance_to(self.start_location) < 32
        )

        if not self.cloak_threat:
            if enemies.filter(
                lambda u: (u.is_cloaked and u.type_id not in SCOUT_IGNORE)
                or u.type_id in CLOAK_UNIT_HINTS
            ):
                self.cloak_threat = self._cloak_seen_live = True
            elif self.enemy_structures.of_type(CLOAK_STRUCT_HINTS):
                self.cloak_threat = self._cloak_seen_live = True

        # Track the largest enemy air force we have ever seen (sticky).
        air_now = enemies.filter(
            lambda u: u.is_flying and (u.type_id in AIR_THREAT_TYPES)
        ).amount
        if air_now > self._max_air_threat:
            self._max_air_threat = air_now
        if air_now > self._air_seen_live:
            self._air_seen_live = air_now

        # Request a scan on cloaked enemies near our stuff.
        cloaked = enemies.filter(lambda u: u.is_cloaked)
        if cloaked and self.townhalls:
            close = cloaked.closest_to(self.start_location)
            own_stuff = self.units | self.structures
            if own_stuff and close.distance_to(own_stuff.closest_to(close)) < 12:
                self._scan_request = close.position

        if not self.enemy_main_visited:
            try:
                if self.is_visible(self.enemy_start_locations[0]):
                    self.enemy_main_visited = True
            except Exception:
                pass
        if not self.enemy_natural_visited:
            try:
                if self.is_visible(self.enemy_natural):
                    self.enemy_natural_visited = True
            except Exception:
                pass

        # Composition tracking for unit-mix decisions.
        ground_combat = enemies.filter(
            lambda u: not u.is_flying
            and u.type_id not in WORKER_TYPES
            and u.type_id not in IGNORE_TARGETS
        )
        armored = ground_combat.filter(lambda u: u.is_armored).amount
        light = ground_combat.filter(lambda u: u.is_light).amount
        if armored > self._armored_seen_max:
            self._armored_seen_max = armored
        if light > self._light_seen_max:
            self._light_seen_max = light
        heavies = enemies.of_type(
            {UnitTypeId.BATTLECRUISER, UnitTypeId.CARRIER, UnitTypeId.TEMPEST, UnitTypeId.BROODLORD}
        ).amount
        if heavies > self._heavy_air_seen:
            self._heavy_air_seen = heavies
        if not self._splash_threat and enemies.filter(
            lambda u: u.type_id in {
                UnitTypeId.BANELING, UnitTypeId.SIEGETANKSIEGED, UnitTypeId.LURKERMP,
                UnitTypeId.LURKERMPBURROWED, UnitTypeId.DISRUPTOR, UnitTypeId.HIGHTEMPLAR,
                UnitTypeId.INFESTOR, UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED,
            }
        ):
            self._splash_threat = True

        self._scout_intel()

        # Feed the opponent profile (live observations only, not preseeded flags).
        if self.strategy:
            if self._rush_seen_live:
                self.strategy.observe("rushed")
            if self.worker_rush_active:
                self.strategy.observe("worker_rush")
            if self.cannon_targets:
                self.strategy.observe("cannon_rush")
            if self._cloak_seen_live:
                self.strategy.observe("cloak")
            if self._air_seen_live >= 3:
                self.strategy.observe("air")
                self.strategy.observe("max_air", self._air_seen_live)

    # ------------------------------------------------------------------ macro

    async def manage_supply(self, cfg):
        if self.supply_cap >= 200:
            return
        if self.supply_used < 13:
            return
        production = (
            self.structures(UnitTypeId.BARRACKS).ready.amount
            + self.structures(UnitTypeId.FACTORY).ready.amount
            + self.structures(UnitTypeId.STARPORT).ready.amount
        )
        threshold = min(16, 3 + 2 * max(1, production))
        pending_cap = 2 if (self.minerals > 500 and self.supply_cap > 60) else 1
        if self.supply_left <= 0 and self.minerals >= 150:
            pending_cap = max(pending_cap, 2)  # blocked with money banked: double up
        if self.supply_left >= threshold:
            return
        if self.already_pending(UnitTypeId.SUPPLYDEPOT) >= pending_cap:
            return
        if not self.can_afford(UnitTypeId.SUPPLYDEPOT):
            return

        # Fill the ramp wall first.
        if self.active_build != "proxy_2rax" and self._wall_positions:
            depots = self.structures.of_type(DEPOT_TYPES)
            empty = [
                p for p in self._wall_positions
                if not depots or depots.closest_distance_to(p) > 1.5
            ]
            if empty:
                workers = self.workers.gathering
                if workers:
                    workers.random.build(UnitTypeId.SUPPLYDEPOT, empty[0])
                return

        if self.townhalls:
            near = self.townhalls.first.position.towards(self.game_info.map_center, 7)
            await self.build(UnitTypeId.SUPPLYDEPOT, near=near)

    async def manage_workers(self, cfg):
        if self.supply_left <= 0:
            return
        if self.supply_workers + self.already_pending(UnitTypeId.SCV) >= cfg["worker_cap"]:
            return
        for th in self.townhalls.ready.idle:
            if self.can_afford(UnitTypeId.SCV):
                th.train(UnitTypeId.SCV)

    async def manage_orbitals(self):
        # Morph CCs: orbitals near main/natural (mule income), PFs on far bases.
        ebay_ready = bool(self.structures(UnitTypeId.ENGINEERINGBAY).ready)
        orbital_tech = self.tech_requirement_progress(UnitTypeId.ORBITALCOMMAND) == 1
        for cc in self.townhalls(UnitTypeId.COMMANDCENTER).ready.idle:
            near_home = (
                cc.distance_to(self.start_location) < 15
                or cc.distance_to(self.natural_position) < 10
            )
            if near_home or not ebay_ready or self.time < 400:
                if orbital_tech and self.can_afford(UnitTypeId.ORBITALCOMMAND):
                    cc(AbilityId.UPGRADETOORBITAL_ORBITALCOMMAND)
            elif self.can_afford(UnitTypeId.PLANETARYFORTRESS):
                cc(AbilityId.UPGRADETOPLANETARYFORTRESS_PLANETARYFORTRESS)

        scan_target = self._scan_request
        self._scan_request = None
        for oc in self.townhalls(UnitTypeId.ORBITALCOMMAND).ready:
            if scan_target is not None and oc.energy >= 50:
                oc(AbilityId.SCANNERSWEEP_SCAN, scan_target)
                scan_target = None
                continue
            reserve = 50 if self.cloak_threat else 0
            if oc.energy >= 50 + reserve or oc.energy >= 195:
                mfs = self.mineral_field.closer_than(10, oc)
                if not mfs and self.townhalls:
                    mfs = self.mineral_field.closer_than(10, self.townhalls.first)
                if mfs:
                    oc(AbilityId.CALLDOWNMULE_CALLDOWNMULE, max(mfs, key=lambda x: x.mineral_contents))

    async def manage_gas(self, cfg):
        target = cfg["gas_target"]
        if target <= 0:
            return
        if not (self.structures(UnitTypeId.BARRACKS) or self.already_pending(UnitTypeId.BARRACKS)):
            return
        current = self.gas_buildings.amount + self.already_pending(UnitTypeId.REFINERY)
        if current >= target:
            return
        if not self.can_afford(UnitTypeId.REFINERY):
            return
        for th in self.townhalls.ready:
            for vg in self.vespene_geyser.closer_than(10, th):
                if self.gas_buildings.filter(lambda u: u.distance_to(vg) < 1):
                    continue
                worker = self.select_build_worker(vg.position)
                if worker is not None:
                    worker.build_gas(vg)
                    return

    def _wants_expand(self, cfg):
        t = self.time + (self.strategy.greed_expand_shift if self.strategy else 0)
        bases = self.townhalls.amount
        if self._proxying or self._base_threats:
            return False
        # Failed pushes: stop banging heads, take a base and grow instead.
        if self._retreat_count >= 2 and bases == 1 and t > 240:
            return True
        prod = {"1rax": 1, "2rax": 2, "3rax": 3}.get(getattr(self.strategy, "production", "2rax"), 2)
        if bases == 1:
            started = (
                self.structures(UnitTypeId.BARRACKS).amount
                + self.already_pending(UnitTypeId.BARRACKS)
            )
            if started < prod:
                return False
            base_time = 95 + 45 * (prod - 1)
            if self.enemy_rush_detected:
                base_time += 60
            return t > base_time
        if bases == 2:
            return t > 330
        if bases == 3:
            return self.minerals > 500 and t > 480
        return self.minerals > 600 and t > 540

    async def manage_expansion(self, cfg):
        if not self._wants_expand(cfg):
            return
        if self.already_pending(UnitTypeId.COMMANDCENTER):
            return
        if not self.can_afford(UnitTypeId.COMMANDCENTER):
            return
        location = await self.get_next_expansion()
        if location is None:
            return
        nearby_enemies = self.enemy_units.filter(lambda u: u.distance_to(location) < 12)
        if nearby_enemies.amount >= 2:
            return
        worker = self.select_build_worker(location)
        if worker is not None and self.can_afford(UnitTypeId.COMMANDCENTER):
            worker.build(UnitTypeId.COMMANDCENTER, location)

    async def _build_at(self, type_id, near, step=7):
        location = await self.find_placement(type_id, near, placement_step=step)
        if location is None:
            return False
        worker = self.select_build_worker(location)
        if worker is None:
            return False
        worker.build(type_id, location)
        return True

    async def manage_production(self, cfg):
        if self.active_build == "proxy_2rax":
            return
        t = self.time

        # Barracks
        rax_total = (
            self.structures.of_type({UnitTypeId.BARRACKS, UnitTypeId.BARRACKSFLYING}).amount
            + self.already_pending(UnitTypeId.BARRACKS)
        )
        if (
            rax_total < cfg["rax_cap"]
            and self.can_afford(UnitTypeId.BARRACKS)
            and self.tech_requirement_progress(UnitTypeId.BARRACKS) == 1
        ):
            if rax_total == 0 and self._rax_wall_position is not None:
                worker = self.select_build_worker(self._rax_wall_position)
                if worker is not None:
                    worker.build(UnitTypeId.BARRACKS, self._rax_wall_position)
            else:
                near = self.start_location.towards(self.game_info.map_center, 9)
                await self._build_at(UnitTypeId.BARRACKS, near, step=7)

        # Bunker at the natural when a rush is coming.
        if cfg["want_bunker"] and t < 420 and self.structures(UnitTypeId.BARRACKS).ready:
            has_nat_cc = self.townhalls.filter(
                lambda th: th.distance_to(self.natural_position) < 8
            )
            bunkers = self.structures(UnitTypeId.BUNKER)
            if (
                has_nat_cc
                and not bunkers.closer_than(10, self.natural_position)
                and self.already_pending(UnitTypeId.BUNKER) == 0
                and self.can_afford(UnitTypeId.BUNKER)
            ):
                near = self.natural_position.towards(self.game_info.map_center, 4)
                await self._build_at(UnitTypeId.BUNKER, near, step=2)

        # Factory
        if cfg["want_factory"] and self.tech_requirement_progress(UnitTypeId.FACTORY) == 1:
            fact_total = (
                self.structures.of_type({UnitTypeId.FACTORY, UnitTypeId.FACTORYFLYING}).amount
                + self.already_pending(UnitTypeId.FACTORY)
            )
            if fact_total < cfg["factory_cap"] and self.can_afford(UnitTypeId.FACTORY):
                near = self.start_location.towards(self.game_info.map_center, 12)
                await self._build_at(UnitTypeId.FACTORY, near, step=7)

        # Starport
        if cfg["want_starport"] and self.tech_requirement_progress(UnitTypeId.STARPORT) == 1:
            port_total = (
                self.structures.of_type({UnitTypeId.STARPORT, UnitTypeId.STARPORTFLYING}).amount
                + self.already_pending(UnitTypeId.STARPORT)
            )
            if port_total < cfg["starport_cap"] and self.can_afford(UnitTypeId.STARPORT):
                near = self.start_location.towards(self.game_info.map_center, 12)
                await self._build_at(UnitTypeId.STARPORT, near, step=7)

        # Engineering bay
        if cfg["want_ebay"]:
            ebay_total = (
                self.structures(UnitTypeId.ENGINEERINGBAY).amount
                + self.already_pending(UnitTypeId.ENGINEERINGBAY)
            )
            if ebay_total < 1 and self.can_afford(UnitTypeId.ENGINEERINGBAY):
                near = self.townhalls.first.position.towards(self.game_info.map_center, 6)
                await self._build_at(UnitTypeId.ENGINEERINGBAY, near, step=4)

        # Armory unlocks heavy mech and later upgrades.
        if cfg["want_armory"] or self.already_pending_upgrade(UpgradeId.TERRANINFANTRYWEAPONSLEVEL1) > 0.4:
            armory_total = (
                self.structures(UnitTypeId.ARMORY).amount + self.already_pending(UnitTypeId.ARMORY)
            )
            if armory_total < 1 and self.can_afford(UnitTypeId.ARMORY):
                near = self.start_location.towards(self.game_info.map_center, 10)
                await self._build_at(UnitTypeId.ARMORY, near, step=4)

        if cfg["want_ghost_academy"] and self.structures(UnitTypeId.BARRACKS).ready:
            total = self.structures(UnitTypeId.GHOSTACADEMY).amount + self.already_pending(UnitTypeId.GHOSTACADEMY)
            if total == 0 and self.can_afford(UnitTypeId.GHOSTACADEMY):
                await self._build_at(UnitTypeId.GHOSTACADEMY, self.start_location, step=4)

        if cfg["want_fusion"] and self.structures(UnitTypeId.STARPORT).ready:
            total = self.structures(UnitTypeId.FUSIONCORE).amount + self.already_pending(UnitTypeId.FUSIONCORE)
            if total == 0 and self.can_afford(UnitTypeId.FUSIONCORE):
                await self._build_at(UnitTypeId.FUSIONCORE, self.start_location, step=4)

        # Missile turrets at every mining base.
        if cfg["want_turrets"] and self.structures(UnitTypeId.ENGINEERINGBAY).ready:
            if self.can_afford(UnitTypeId.MISSILETURRET):
                needed = 2 if (self.cloak_threat or self._max_air_threat >= 3) else 1
                for th in self.townhalls.ready:
                    if self.structures(UnitTypeId.MISSILETURRET).closer_than(9, th).amount >= needed:
                        continue
                    mfs = self.mineral_field.closer_than(10, th)
                    near = mfs.center.towards(th.position, 2) if mfs else th.position
                    if await self._build_at(UnitTypeId.MISSILETURRET, near, step=2):
                        break

    async def manage_proxy(self):
        if not self._proxying:
            return
        alive = self.workers.tags
        self.proxy_scv_tags = [tag for tag in self.proxy_scv_tags if tag in alive]

        if self.time > 20 and len(self.proxy_scv_tags) < 2 and self.workers.amount >= 12:
            candidates = self.workers.gathering.sorted(lambda w: w.distance_to(self.proxy_point))
            for worker in candidates:
                if len(self.proxy_scv_tags) >= 2:
                    break
                if worker.tag in self.proxy_scv_tags:
                    continue
                self.proxy_scv_tags.append(worker.tag)
                worker.move(self.proxy_point)

        proxy_scvs = self.workers.tags_in(self.proxy_scv_tags)
        rax_total = self.structures(UnitTypeId.BARRACKS).amount + self.already_pending(UnitTypeId.BARRACKS)
        base_rax = max(2, {"1rax": 1, "2rax": 2, "3rax": 3}.get(getattr(self.strategy, "production", "2rax"), 2))
        rax_cap = base_rax if self.minerals < 400 else base_rax + 1
        if rax_total < rax_cap and self.can_afford(UnitTypeId.BARRACKS):
            for worker in proxy_scvs:
                if worker.distance_to(self.proxy_point) < 12 and not worker.is_constructing_scv:
                    location = await self.find_placement(
                        UnitTypeId.BARRACKS, self.proxy_point, max_distance=16, placement_step=4
                    )
                    if location is not None:
                        worker.build(UnitTypeId.BARRACKS, location)
                        break

        for worker in proxy_scvs:
            if worker.is_idle:
                if self.attack_mode and self.time > 150:
                    worker.attack(self.enemy_start_locations[0])
                else:
                    worker.move(self.proxy_point)

    def _addon_space_free(self, position):
        addon_position = position + Point2((2.5, -0.5))
        points = [
            (addon_position + Point2((x - 0.5, y - 0.5))).rounded
            for x in range(0, 2)
            for y in range(0, 2)
        ]
        return all(
            self.in_map_bounds(p) and self.in_placement_grid(p) and self.in_pathing_grid(p)
            for p in points
        )

    async def manage_addons(self, cfg):
        if self.active_build == "proxy_2rax":
            return
        techlab_total = (
            self.structures(UnitTypeId.BARRACKSTECHLAB).amount
            + self.already_pending(UnitTypeId.BARRACKSTECHLAB)
        )
        for rax in self.structures(UnitTypeId.BARRACKS).ready.idle:
            if rax.has_add_on:
                continue
            want_techlab = techlab_total < cfg["techlab_cap"]
            addon = UnitTypeId.BARRACKSTECHLAB if want_techlab else UnitTypeId.BARRACKSREACTOR
            if not self.can_afford(addon):
                continue
            if self._addon_space_free(rax.position):
                rax.build(addon)
                if want_techlab:
                    techlab_total += 1

        army = getattr(self.strategy, "army", "mixed") if self.strategy else "mixed"
        factory_labs = self.structures(UnitTypeId.FACTORYTECHLAB).amount
        for factory in self.structures(UnitTypeId.FACTORY).ready.idle:
            if factory.has_add_on or not self._addon_space_free(factory.position):
                continue
            want_lab = army in {"mech", "mixed"} and factory_labs < max(1, cfg["factory_cap"] - 1)
            addon = UnitTypeId.FACTORYTECHLAB if want_lab else UnitTypeId.FACTORYREACTOR
            if self.can_afford(addon):
                factory.build(addon)
                factory_labs += int(want_lab)

        port_labs = self.structures(UnitTypeId.STARPORTTECHLAB).amount
        for port in self.structures(UnitTypeId.STARPORT).ready.idle:
            if port.has_add_on or not self._addon_space_free(port.position):
                continue
            want_lab = army in {"sky", "mixed"} and port_labs < max(1, cfg["starport_cap"] - 1)
            addon = UnitTypeId.STARPORTTECHLAB if want_lab else UnitTypeId.STARPORTREACTOR
            if self.can_afford(addon):
                port.build(addon)
                port_labs += int(want_lab)

    async def manage_upgrades(self, cfg):
        if self.active_build == "proxy_2rax":
            return
        for techlab in self.structures(UnitTypeId.BARRACKSTECHLAB).ready.idle:
            if self.already_pending_upgrade(UpgradeId.STIMPACK) == 0 and self.can_afford(UpgradeId.STIMPACK):
                techlab.research(UpgradeId.STIMPACK)
            elif (
                self.already_pending_upgrade(UpgradeId.STIMPACK) == 1
                and self.already_pending_upgrade(UpgradeId.SHIELDWALL) == 0
                and self.can_afford(UpgradeId.SHIELDWALL)
            ):
                techlab.research(UpgradeId.SHIELDWALL)
            elif (
                cfg["marauder"]
                and self.already_pending_upgrade(UpgradeId.SHIELDWALL) == 1
                and self.already_pending_upgrade(UpgradeId.PUNISHERGRENADES) == 0
                and self.can_afford(UpgradeId.PUNISHERGRENADES)
            ):
                techlab.research(UpgradeId.PUNISHERGRENADES)

        armory_ready = bool(self.structures(UnitTypeId.ARMORY).ready)
        infantry_upgrades = [
            (UpgradeId.TERRANINFANTRYWEAPONSLEVEL1, False),
            (UpgradeId.TERRANINFANTRYARMORSLEVEL1, False),
            (UpgradeId.TERRANINFANTRYWEAPONSLEVEL2, True),
            (UpgradeId.TERRANINFANTRYARMORSLEVEL2, True),
            (UpgradeId.TERRANINFANTRYWEAPONSLEVEL3, True),
            (UpgradeId.TERRANINFANTRYARMORSLEVEL3, True),
        ]
        for ebay in self.structures(UnitTypeId.ENGINEERINGBAY).ready.idle:
            for upgrade, needs_armory in infantry_upgrades:
                if needs_armory and not armory_ready:
                    continue
                if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                    ebay.research(upgrade)
                    break

        army_plan = getattr(self.strategy, "army", "mixed") if self.strategy else "mixed"
        armory_upgrades = []
        if army_plan in {"mech", "mixed"}:
            armory_upgrades.extend([
                UpgradeId.TERRANVEHICLEWEAPONSLEVEL1,
                UpgradeId.TERRANVEHICLEWEAPONSLEVEL2,
                UpgradeId.TERRANVEHICLEWEAPONSLEVEL3,
            ])
        if army_plan in {"sky", "mixed"}:
            armory_upgrades.extend([
                UpgradeId.TERRANSHIPWEAPONSLEVEL1,
                UpgradeId.TERRANSHIPWEAPONSLEVEL2,
                UpgradeId.TERRANSHIPWEAPONSLEVEL3,
            ])
        armory_upgrades.extend([
            UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL1,
            UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL2,
            UpgradeId.TERRANVEHICLEANDSHIPARMORSLEVEL3,
        ])
        for armory in self.structures(UnitTypeId.ARMORY).ready.idle:
            for upgrade in armory_upgrades:
                if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                    armory.research(upgrade)
                    break

        starport_upgrades = [UpgradeId.RAVENCORVIDREACTOR, UpgradeId.BANSHEECLOAK]
        for techlab in self.structures(UnitTypeId.STARPORTTECHLAB).ready.idle:
            for upgrade in starport_upgrades:
                if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                    techlab.research(upgrade)
                    break

        for core in self.structures(UnitTypeId.FUSIONCORE).ready.idle:
            upgrade = UpgradeId.BATTLECRUISERENABLESPECIALIZATIONS
            if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                core.research(upgrade)

    async def train_army(self, cfg):
        reactor_tags = self.structures(UnitTypeId.BARRACKSREACTOR).tags
        techlab_tags = self.structures(UnitTypeId.BARRACKSTECHLAB).tags
        port_reactor_tags = self.structures(UnitTypeId.STARPORTREACTOR).tags
        port_techlab_tags = self.structures(UnitTypeId.STARPORTTECHLAB).tags
        fact_techlab_tags = self.structures(UnitTypeId.FACTORYTECHLAB).tags
        fact_reactor_tags = self.structures(UnitTypeId.FACTORYREACTOR).tags
        army_plan = getattr(self.strategy, "army", "mixed") if self.strategy else "mixed"

        viking_target = min(12, max(2 if army_plan == "sky" else 0, self._max_air_threat + 1 + 2 * self._heavy_air_seen))
        vikings_have = self.units.of_type(VIKING_TYPES).amount + self.already_pending(UnitTypeId.VIKINGFIGHTER)
        medivacs_have = self.units(UnitTypeId.MEDIVAC).amount + self.already_pending(UnitTypeId.MEDIVAC)
        ravens = self.units(UnitTypeId.RAVEN).amount + self.already_pending(UnitTypeId.RAVEN)
        banshees = self.units(UnitTypeId.BANSHEE).amount + self.already_pending(UnitTypeId.BANSHEE)
        liberators = self.units.of_type({UnitTypeId.LIBERATOR, UnitTypeId.LIBERATORAG}).amount + self.already_pending(UnitTypeId.LIBERATOR)
        battlecruisers = self.units(UnitTypeId.BATTLECRUISER).amount + self.already_pending(UnitTypeId.BATTLECRUISER)

        for port in self.structures(UnitTypeId.STARPORT).ready:
            if self.supply_left < 2:
                break
            can_queue = port.is_idle or (port.add_on_tag in port_reactor_tags and len(port.orders) < 2)
            if not can_queue:
                continue
            if vikings_have < viking_target and self.can_afford(UnitTypeId.VIKINGFIGHTER):
                # Do not let luxury tech delay the hard anti-air response.
                port.train(UnitTypeId.VIKINGFIGHTER)
                vikings_have += 1
            elif (
                port.add_on_tag in port_techlab_tags
                and self.structures(UnitTypeId.FUSIONCORE).ready
                and battlecruisers < max(1, self.townhalls.amount // 2)
                and self.supply_left >= 6
                and self.can_afford(UnitTypeId.BATTLECRUISER)
            ):
                port.train(UnitTypeId.BATTLECRUISER)
                battlecruisers += 1
            elif port.add_on_tag in port_techlab_tags and ravens < 2 and self.can_afford(UnitTypeId.RAVEN):
                port.train(UnitTypeId.RAVEN)
                ravens += 1
            elif port.add_on_tag in port_techlab_tags and banshees <= liberators and self.can_afford(UnitTypeId.BANSHEE):
                port.train(UnitTypeId.BANSHEE)
                banshees += 1
            elif cfg["medivac_cap"] > 0 and medivacs_have < cfg["medivac_cap"] and self.can_afford(UnitTypeId.MEDIVAC):
                port.train(UnitTypeId.MEDIVAC)
                medivacs_have += 1
            elif army_plan in {"sky", "mixed"} and self.can_afford(UnitTypeId.LIBERATOR):
                port.train(UnitTypeId.LIBERATOR)
                liberators += 1

        tanks = self.units.of_type(TANK_TYPES).amount + self.already_pending(UnitTypeId.SIEGETANK)
        cyclones = self.units(UnitTypeId.CYCLONE).amount + self.already_pending(UnitTypeId.CYCLONE)
        thors = self.units(UnitTypeId.THOR).amount + self.already_pending(UnitTypeId.THOR)
        hellions = self.units.of_type({UnitTypeId.HELLION, UnitTypeId.HELLIONTANK}).amount + self.already_pending(UnitTypeId.HELLION)
        mines = self.units.of_type({UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED}).amount + self.already_pending(UnitTypeId.WIDOWMINE)
        for factory in self.structures(UnitTypeId.FACTORY).ready:
            can_queue = factory.is_idle or (factory.add_on_tag in fact_reactor_tags and len(factory.orders) < 2)
            if not can_queue or self.supply_left < 2:
                continue
            if factory.add_on_tag in fact_techlab_tags:
                if self.structures(UnitTypeId.ARMORY).ready and thors <= tanks // 2 and self.supply_left >= 6 and self.can_afford(UnitTypeId.THOR):
                    factory.train(UnitTypeId.THOR)
                    thors += 1
                elif tanks < cfg["tank_cap"] and tanks <= cyclones and self.supply_left >= 3 and self.can_afford(UnitTypeId.SIEGETANK):
                    factory.train(UnitTypeId.SIEGETANK)
                    tanks += 1
                elif army_plan in {"mech", "mixed"} and self.can_afford(UnitTypeId.CYCLONE):
                    factory.train(UnitTypeId.CYCLONE)
                    cyclones += 1
            elif army_plan in {"mech", "mixed"}:
                if mines <= hellions // 3 and self.can_afford(UnitTypeId.WIDOWMINE):
                    factory.train(UnitTypeId.WIDOWMINE)
                    mines += 1
                elif self.can_afford(UnitTypeId.HELLION):
                    factory.train(UnitTypeId.HELLION)
                    hellions += 1

        for rax in self.structures(UnitTypeId.BARRACKS).ready:
            if self.supply_left < 1:
                break
            if rax.add_on_tag in techlab_tags and cfg["marauder"]:
                # Marauders against armored comps (roach/stalker/tank), marines vs light.
                prefer_marauder = (
                    self._armored_seen_max >= self._light_seen_max
                    or self.vespene > 300
                    or (self.strategy and self.strategy.tech == "marauder_bio")
                )
                if rax.is_idle:
                    ghosts = self.units(UnitTypeId.GHOST).amount + self.already_pending(UnitTypeId.GHOST)
                    reapers = self.units(UnitTypeId.REAPER).amount + self.already_pending(UnitTypeId.REAPER)
                    if self.structures(UnitTypeId.GHOSTACADEMY).ready and ghosts < 2 and self.can_afford(UnitTypeId.GHOST):
                        rax.train(UnitTypeId.GHOST)
                    elif reapers < 2 and self.can_afford(UnitTypeId.REAPER):
                        rax.train(UnitTypeId.REAPER)
                    elif prefer_marauder and self.can_afford(UnitTypeId.MARAUDER) and self.vespene >= 25:
                        rax.train(UnitTypeId.MARAUDER)
                    elif self.can_afford(UnitTypeId.MARINE):
                        rax.train(UnitTypeId.MARINE)
            else:
                queue_limit = 2 if rax.add_on_tag in reactor_tags else 1
                if len(rax.orders) < queue_limit and self.can_afford(UnitTypeId.MARINE):
                    rax.train(UnitTypeId.MARINE)

    # ------------------------------------------------------------- scout/def

    async def manage_scout(self):
        if self.active_build == "proxy_2rax":
            return
        if not self.scout_sent and self.supply_used >= 14:
            workers = self.workers.gathering
            if workers:
                scout = workers.closest_to(self.game_info.map_center)
                self.scout_tag = scout.tag
                self.scout_sent = True
                scout.move(self.enemy_natural.towards(self.game_info.map_center, 3))
                scout.move(self.enemy_start_locations[0], queue=True)
        if self.scout_tag is None:
            return
        scout = self.workers.find_by_tag(self.scout_tag)
        if scout is None:
            self.scout_tag = None
            return
        if scout.health < 15:
            self.scout_tag = None
            if self.townhalls:
                mfs = self.mineral_field.closer_than(10, self.townhalls.first)
                if mfs:
                    scout.gather(mfs.random)
            return
        if scout.is_idle:
            if self.time < 240:
                # Early on, sweep a proxy pocket first - the ladder is full of
                # hidden proxies, and a scout that only checks the enemy main
                # never sees them.
                if not self._proxy_alert and self.time < 170:
                    scout.move(self._next_proxy_spot())
                    scout.move(self.enemy_natural.towards(self.game_info.map_center, 4), queue=True)
                else:
                    scout.move(self.enemy_natural.towards(self.game_info.map_center, 4))
                scout.move(self.enemy_start_locations[0].towards(self.enemy_natural, 6), queue=True)
            else:
                self.scout_tag = None
                if self.townhalls:
                    mfs = self.mineral_field.closer_than(10, self.townhalls.first)
                    if mfs:
                        scout.gather(mfs.random)

    async def manage_worker_safety(self):
        """Run workers away from raiders in the mineral line (reapers, oracles,
        hellions, mutas, DTs, void rays). The ladder meta is harass-heavy; letting
        workers mine into a reaper or oracle bleeds the economy that wins games.
        Only pulls the workers actually in danger, and only when our army/static
        defense at that base can't already cover it."""
        harassers = self.enemy_units.filter(lambda u: u.type_id in HARASSER_TYPES)
        if not harassers:
            return
        army = self.units.of_type(ARMY_TYPES)
        defense = self.structures.of_type(DEFENSIVE_STRUCTS)
        for th in self.townhalls:
            near = harassers.filter(lambda h: h.distance_to(th) < 13)
            if not near:
                continue
            guards = army.closer_than(13, th).amount + 2 * defense.closer_than(13, th).amount
            if guards >= near.amount * 3:
                continue  # our defenders can handle this raid; keep mining
            # Bases with no raider nearby are safe havens to mine at instead.
            safe_bases = self.townhalls.filter(
                lambda t: harassers.closest_distance_to(t) > 16
            )
            for h in near:
                threatened = self.workers.filter(
                    lambda w: (w.is_gathering or w.is_returning or w.is_idle)
                    and w.distance_to(h) < 7
                )
                for w in threatened:
                    if safe_bases:
                        haven = safe_bases.closest_to(w)
                        mfs = self.mineral_field.closer_than(10, haven)
                        if mfs:
                            w.gather(mfs.closest_to(haven))
                            continue
                    # No safe base: step directly away from the raider.
                    self._flee(w, h.position, 5)

    def _scout_intel(self):
        """Turn raw vision into answers to the questions that decide games:
        how many bases does the enemy have, is there a proxy, and does the
        absence of an expansion imply an all-in? Feeds the same rush/defense
        and per-opponent profile systems the rest of the bot already uses."""
        t = self.time
        enemy_main = self.enemy_start_locations[0]

        # 1) Count enemy bases (sticky max).
        visible_bases = self.enemy_structures.of_type(ENEMY_TOWNHALLS)
        if visible_bases.amount > self._enemy_base_count:
            self._enemy_base_count = visible_bases.amount

        # 2) Proxy hunt: a production/tech/defensive building that is closer to us
        #    than to the enemy main is a proxy - treat it as incoming aggression.
        if not self._proxy_alert:
            for s in self.enemy_structures:
                if s.type_id not in SCOUT_PROXY_STRUCTS:
                    continue
                d_main = s.distance_to(enemy_main)
                if d_main > 45 and s.distance_to(self.start_location) < d_main:
                    self._proxy_alert = True
                    self.enemy_rush_detected = self._rush_seen_live = True
                    if self.strategy:
                        self.strategy.observe("rushed")
                        self.strategy.observe("cannon_rush")
                    break

        # 3) Reasoning from absence: if we can SEE the enemy natural and there is
        #    no townhall there past the point they should have expanded, and they
        #    are still on one base, an all-in is very likely -> prepare to defend.
        if not self._all_in_suspected and 90 < t < 240:
            try:
                nat_visible = self.is_visible(self.enemy_natural)
            except Exception:
                nat_visible = False
            if nat_visible:
                nat_taken = self.enemy_structures.of_type(ENEMY_TOWNHALLS).closer_than(
                    9, self.enemy_natural
                )
                if not nat_taken and self._enemy_base_count <= 1:
                    self._all_in_suspected = True
                    self.enemy_rush_detected = self._rush_seen_live = True
                    if self.strategy:
                        self.strategy.observe("rushed")

    def _next_proxy_spot(self):
        """A place worth checking for a hidden proxy: map center, then the
        midpoints between center and our base (common proxy pocket)."""
        center = self.game_info.map_center
        options = [
            center,
            center.towards(self.start_location, 18),
            self.start_location.towards(center, 30),
        ]
        idx = int(self.time / 12) % len(options)
        return options[idx]

    async def manage_defense(self):
        t = self.time

        # Cannon rush / proxy structure response: pull nearby SCVs.
        if self.cannon_targets and t < 360:
            for target in self.cannon_targets:
                already_on_it = self.workers.filter(
                    lambda w: w.order_target == target.tag
                ).amount
                if already_on_it >= 3:
                    continue
                helpers = self.workers.filter(
                    lambda w: w.is_gathering or w.is_idle
                ).sorted(lambda w: w.distance_to(target))
                count = already_on_it
                for worker in helpers:
                    if count >= 3:
                        break
                    worker.attack(target)
                    count += 1

        # Worker rush response.
        if self.worker_rush_active:
            invaders = self.enemy_units.filter(
                lambda u: u.type_id in WORKER_TYPES and u.distance_to(self.start_location) < 22
            )
            if invaders:
                needed = min(self.workers.amount, invaders.amount + 2)
                fighters = self.workers.sorted(lambda w: w.distance_to(self.start_location))
                assigned = 0
                for worker in fighters:
                    if assigned >= needed:
                        break
                    if worker.is_gathering or worker.is_idle or worker.is_moving:
                        worker.attack(invaders.closest_to(worker))
                    assigned += 1

        # Track enemy forces near any of our bases.
        ths = self.townhalls
        nearby = self.enemy_units.filter(
            lambda u: u.type_id not in SCOUT_IGNORE
            and u.type_id not in IGNORE_TARGETS
            and any(u.distance_to(th) < 25 for th in ths)
        )
        real_threats = nearby.filter(lambda u: u.type_id not in WORKER_TYPES)
        worker_intruders = nearby.of_type(WORKER_TYPES)
        if real_threats:
            threats = nearby if worker_intruders else real_threats
        elif worker_intruders.amount >= 3:
            threats = worker_intruders
        else:
            threats = None  # a lone scouting worker is not an invasion
        self._base_threats = threats if threats else None
        if threats and not self._first_pressure_recorded:
            self._first_pressure_recorded = True
            if self.strategy:
                self.strategy.observe("first_pressure", t)

        # Bunker load/unload.
        bunkers = self.structures(UnitTypeId.BUNKER).ready
        if bunkers:
            if threats:
                self._bunker_last_threat = t
            any_close_threats = False
            for bunker in bunkers:
                close_threats = threats.closer_than(20, bunker) if threats else None
                if close_threats:
                    any_close_threats = True
                    marines = self.units(UnitTypeId.MARINE).closer_than(14, bunker)
                    for marine in marines:
                        if len(self._bunker_assignees) >= 4:
                            break
                        self._bunker_assignees.add(marine.tag)
                    for marine in marines.tags_in(self._bunker_assignees):
                        marine.smart(bunker)
                elif t - self._bunker_last_threat > 12 and int(t) % 6 == 0:
                    bunker(AbilityId.UNLOADALL_BUNKER)
            if not any_close_threats:
                self._bunker_assignees.clear()

        # Repair damaged key structures under fire (wall, bunkers, turrets, PFs).
        if threats and t < 600:
            try:
                ramp_top = self.main_base_ramp.top_center
            except Exception:
                ramp_top = self.start_location
            damaged = self.structures.filter(
                lambda s: s.health_percentage < 0.97
                and (
                    s.distance_to(ramp_top) < 8
                    or s.type_id in {
                        UnitTypeId.BUNKER, UnitTypeId.PLANETARYFORTRESS, UnitTypeId.MISSILETURRET
                    }
                )
                and threats.closer_than(14, s)
            )
            for structure in damaged:
                helpers = self.workers.filter(
                    lambda w: (w.is_gathering or w.is_idle) and w.distance_to(structure) < 14
                )
                count = 0
                for worker in helpers:
                    if count >= 3:
                        break
                    worker.repair(structure)
                    count += 1

    async def manage_depot_wall(self):
        enemy_ground = self.enemy_units.not_flying
        for depot in self.structures(UnitTypeId.SUPPLYDEPOT).ready:
            if not enemy_ground or enemy_ground.closest_distance_to(depot) > 12:
                depot(AbilityId.MORPH_SUPPLYDEPOT_LOWER)
        for depot in self.structures(UnitTypeId.SUPPLYDEPOTLOWERED).ready:
            if enemy_ground and enemy_ground.closest_distance_to(depot) < 9:
                depot(AbilityId.MORPH_SUPPLYDEPOT_RAISE)

    # ------------------------------------------------------------------ army

    def _attack_target(self):
        army = self.units.of_type(ARMY_TYPES)
        reference = army.center if army else self.start_location
        if self.enemy_structures:
            return self.enemy_structures.closest_to(reference).position
        visible = self.enemy_units.filter(lambda u: u.type_id not in IGNORE_TARGETS)
        if visible:
            return visible.closest_to(reference).position
        # Sweep: natural first (better first engagement than ramming a wall), then main.
        if not self.enemy_natural_visited:
            return self.enemy_natural
        if not self.enemy_main_visited:
            return self.enemy_start_locations[0]
        try:
            locations = self.expansion_locations_list
            return locations[int(self.time / 15) % len(locations)]
        except Exception:
            return self.enemy_start_locations[0]

    async def _desperado(self, iteration):
        target = self._attack_target()
        for unit in self.workers | self.units.of_type(ARMY_TYPES):
            if unit.is_idle or iteration % 20 == 0:
                unit.attack(target)

    # -------- power evaluation (fight or flee)

    @staticmethod
    def _unit_power(u):
        dps = max(u.ground_dps, u.air_dps)
        if dps <= 0:
            dps = CASTER_NOMINAL_DPS.get(u.type_id, 0)
        if dps <= 0:
            return 0.0
        return dps * (u.health + u.shield)

    def _our_power(self, units):
        total = 0.0
        for u in units:
            if u.type_id == UnitTypeId.MEDIVAC:
                total += 4000.0  # healing support value
            else:
                total += self._unit_power(u)
        return total

    def _their_power(self, center, radius=22.0):
        total = 0.0
        for u in self._cached_enemies.closer_than(radius, center):
            total += self._unit_power(u)
        for s in self.enemy_structures.closer_than(radius, center):
            dps = STATIC_DEFENSE_DPS.get(s.type_id, 0)
            if dps and s.is_ready:
                total += dps * (s.health + s.shield)
        return total

    def _collect_dodge_zones(self):
        zones = []
        try:
            for effect in self.state.effects:
                eid = effect.id
                if isinstance(eid, str):
                    continue
                margin = DODGE_EFFECTS.get(eid)
                if margin is None:
                    continue
                try:
                    radius = float(effect.radius)
                except Exception:
                    radius = 1.5
                for pos in effect.positions:
                    zones.append((pos, radius + margin))
        except Exception:
            pass
        self._dodge_zones = zones

    def _dodge(self, unit):
        for pos, radius in self._dodge_zones:
            if unit.distance_to(pos) < radius:
                self._flee(unit, pos, radius + 1.0)
                return True
        return False

    def _flee(self, unit, from_pos, distance):
        try:
            if unit.distance_to(from_pos) < 0.3:
                goal = unit.position.towards(self.start_location, distance)
            else:
                goal = unit.position.towards(from_pos, -distance)
            if not self.in_pathing_grid(goal):
                goal = unit.position.towards(self.start_location, distance)
            unit.move(goal)
        except Exception:
            unit.move(self.staging_point)

    def _defensive_point(self):
        """Best place to hold on defense: the main ramp choke, else staging."""
        try:
            return self.main_base_ramp.top_center
        except Exception:
            return self.staging_point

    async def control_army(self, cfg):
        army = self.units.of_type(ARMY_TYPES)
        if not army:
            return
        bio = army.of_type(BIO_TYPES)

        self._stim_ready = self.already_pending_upgrade(UpgradeId.STIMPACK) == 1
        self._cached_enemies = self.enemy_units.filter(
            lambda u: u.type_id not in IGNORE_TARGETS
        )
        self._focus_board = {}
        self._cached_banelings = self._cached_enemies.of_type({UnitTypeId.BANELING})
        self._cached_air_enemies = self._cached_enemies.filter(lambda u: u.is_flying)
        self._collect_dodge_zones()

        build = self.active_build
        t = self.time
        combat_count = bio.amount if build == "proxy_2rax" else army.amount

        # Attack / regroup state machine.
        if build == "proxy_2rax":
            if bio.amount >= cfg["attack_min_bio"] or t > 170:
                self.attack_mode = True
        else:
            if (
                not self.attack_mode
                and t > self._retreat_until
                and combat_count >= cfg["attack_min_bio"] * (1 + 0.15 * min(3, self._retreat_count))
                and (self._stim_ready or t > 380 or (getattr(self.strategy, "production", "") == "3rax" and t > 320))
            ):
                self.attack_mode = True
            if self.attack_mode and combat_count <= cfg["retreat_bio"]:
                self.attack_mode = False
                self._retreat_until = t + 10
                self._retreat_count += 1
            if self.supply_used > 190:
                self.attack_mode = True

        # Fight-or-flee: disengage clearly losing fights (never during all-ins).
        if self.attack_mode and not self.all_in and army:
            center = army.center
            local_enemies = self._cached_enemies.closer_than(22, center)
            if local_enemies.amount >= 3:
                ours = self._our_power(army.closer_than(22, center))
                theirs = self._their_power(center)
                if theirs > ours * 1.4:
                    self.attack_mode = False
                    self._retreat_until = t + 12
                    self._retreat_count += 1

        # Where should the army be?
        defending = self._base_threats is not None
        if defending:
            threat = self._base_threats.closest_to(self.start_location)
            hold = self._defensive_point()
            # Hold the ramp choke while the enemy is still outside it; engage once
            # they commit past it. High ground + a narrow front win these fights.
            if (
                self.townhalls.amount == 1
                and threat.distance_to(self.start_location) > hold.distance_to(self.start_location) + 2
            ):
                target = hold
            else:
                target = threat.position
        elif self.attack_mode:
            target = self._attack_target()
        elif build == "proxy_2rax":
            target = self.proxy_point
        else:
            if self.enemy_rush_detected or t < 250:
                try:
                    target = self.main_base_ramp.top_center
                except Exception:
                    target = self.staging_point
            else:
                target = self.staging_point

        bio_center = bio.center if bio else None
        self._cached_bio_for_spread = bio if self._splash_threat else None

        for unit in army:
            # Marines told to enter a bunker keep that order.
            if unit.tag in self._bunker_assignees:
                continue
            # Regroup stragglers instead of trickling them into the enemy.
            if (
                self.attack_mode
                and not defending
                and bio_center is not None
                and unit.type_id in BIO_TYPES
                and unit.distance_to(bio_center) > 28
                and not self._cached_enemies.closer_than(11, unit)
            ):
                unit.move(bio_center)
                continue

            if unit.type_id == UnitTypeId.MEDIVAC:
                self._micro_medivac(unit, bio)
            elif unit.type_id in TANK_TYPES:
                self._micro_tank(unit, target, bio_center)
            elif unit.type_id in VIKING_TYPES:
                self._micro_viking(unit, target, bio_center)
            elif self._micro_special(unit, target, bio_center):
                continue
            else:
                self._micro_bio(unit, target, aggressive=self.attack_mode or defending)

        # Proxy all-in SCV pull.
        if (
            build == "proxy_2rax"
            and self.attack_mode
            and not self.scv_pulled_for_allin
            and bio.amount >= 5
            and t > 170
        ):
            self.scv_pulled_for_allin = True
            pulled = 0
            for worker in self.workers:
                if pulled >= 8:
                    break
                if worker.tag in self.proxy_scv_tags:
                    continue
                worker.attack(self.enemy_start_locations[0])
                pulled += 1

    def _concave_offset(self, unit, point):
        """Fan ranged units laterally during the approach so they form a loose
        concave instead of a single stacked blob. Deterministic per unit tag,
        so orders stay stable. No-op unless mid-approach to a nearby enemy."""
        try:
            if not getattr(self, "attack_mode", False):
                return point
            enemies = self._cached_enemies
            if not enemies:
                return point
            nearest = enemies.closest_to(unit)
            d = nearest.distance_to(unit)
            if d < 6 or d > 20:
                return point
            import math
            dx = point.x - unit.position.x
            dy = point.y - unit.position.y
            norm = math.hypot(dx, dy) or 1.0
            px, py = -dy / norm, dx / norm
            lane = ((unit.tag % 7) - 3) * 1.2
            cand = Point2((point.x + px * lane, point.y + py * lane))
            if self.in_pathing_grid(cand):
                return cand
            return point
        except Exception:
            return point

    def _ordered_attack_point(self, unit, point):
        point = self._concave_offset(unit, point)
        cached = self._point_order_cache.get(unit.tag)
        if (
            cached is not None
            and cached[0].distance_to(point) < 3
            and self.time - cached[1] < 2.5
            and not unit.is_idle
        ):
            return
        self._point_order_cache[unit.tag] = (point, self.time)
        unit.attack(point)

    def _preserve_hurt(self, unit, enemies):
        """If low on HP and reloading, retreat from the nearest threat that can
        reach us so the unit survives to keep shooting. Returns True if handled."""
        try:
            if unit.weapon_cooldown == 0 or unit.health_percentage >= 0.35:
                return False
            if not enemies:
                return False
            def reach(e):
                return (e.air_range if unit.is_flying else e.ground_range)
            threats = enemies.filter(
                lambda e: (e.can_attack_air if unit.is_flying else e.can_attack_ground)
                and e.distance_to(unit) < reach(e) + 1.5
            )
            if not threats:
                return False
            self._flee(unit, threats.closest_to(unit).position, 2.5)
            return True
        except Exception:
            return False

    def _best_target(self, unit, enemies, by_distance=False, prefer_armored=False):
        """Kill-secure focus fire: finish low-HP targets, never overkill.

        A per-frame damage board tracks damage our units have already committed
        this step, so once a target has lethal damage queued the next shooter
        moves on to a fresh one instead of wasting shots on a corpse.
        """
        in_range = enemies.filter(lambda e: unit.target_in_range(e))
        if not in_range:
            return None
        board = self._focus_board

        def eff_hp(e):
            return (e.health + e.shield) - board.get(e.tag, 0.0)

        def priority(e):
            if e.type_id in SPECIAL_TARGETS:
                group = 0.0
            elif e.can_attack_ground or e.can_attack_air:
                group = 1.0
            elif e.type_id in WORKER_TYPES:
                group = 2.0
            else:
                group = 3.0
            if prefer_armored and e.is_armored:
                group -= 0.5
            ehp = eff_hp(e)
            overkilled = ehp <= 0            # already lethally committed - avoid
            metric = e.distance_to(unit) if by_distance else ehp
            return (overkilled, group, metric)

        target = min(in_range, key=priority)
        try:
            dmg = unit.calculate_damage_vs_target(target)[0]
        except Exception:
            dmg = max(unit.ground_dps, unit.air_dps) * 0.5
        board[target.tag] = board.get(target.tag, 0.0) + max(1.0, dmg)
        return target

    @staticmethod
    def _threat_range(threat, victim):
        if victim.is_flying:
            return threat.air_range
        return threat.ground_range

    def _can_cast_again(self, unit, ability, cooldown):
        key = (unit.tag, ability)
        if self.time - self._special_last_cast.get(key, -999.0) < cooldown:
            return False
        self._special_last_cast[key] = self.time
        return True

    @staticmethod
    def _cluster_anchor(enemies, radius, minimum):
        best = None
        best_count = minimum - 1
        for enemy in enemies:
            count = enemies.closer_than(radius, enemy).amount
            if count > best_count:
                best = enemy
                best_count = count
        return best

    def _micro_special(self, unit, target_point, army_center):
        """Use high-impact specialist abilities before falling back to weapon micro."""
        enemies = self._cached_enemies

        if unit.type_id == UnitTypeId.GHOST:
            if self._dodge(unit):
                return True
            local = enemies.closer_than(10, unit)
            shielded = local.filter(lambda e: e.shield >= 25)
            emp_target = self._cluster_anchor(shielded, 3.0, 2)
            if (
                unit.energy >= 75
                and emp_target is not None
                and self._can_cast_again(unit, AbilityId.EMP_EMP, 2.5)
            ):
                unit(AbilityId.EMP_EMP, emp_target.position)
                return True
            biological = local.filter(lambda e: e.is_biological and e.health >= 80)
            if (
                unit.energy >= 50
                and biological
                and self._can_cast_again(unit, AbilityId.EFFECT_GHOSTSNIPE, 2.0)
            ):
                unit(AbilityId.EFFECT_GHOSTSNIPE, max(biological, key=lambda e: e.health))
                return True
            return False

        if unit.type_id == UnitTypeId.RAVEN:
            if self._dodge(unit):
                return True
            local = enemies.closer_than(11, unit)
            armor_target = self._cluster_anchor(local, 3.0, 3)
            if (
                unit.energy >= 75
                and armor_target is not None
                and self._can_cast_again(unit, AbilityId.EFFECT_ANTIARMORMISSILE, 5.0)
            ):
                unit(AbilityId.EFFECT_ANTIARMORMISSILE, armor_target)
                return True
            mechanical = local.filter(lambda e: e.is_mechanical and (e.health + e.shield) >= 125)
            if (
                unit.energy >= 75
                and mechanical
                and self._can_cast_again(unit, AbilityId.EFFECT_INTERFERENCEMATRIX, 4.0)
            ):
                unit(AbilityId.EFFECT_INTERFERENCEMATRIX, max(mechanical, key=lambda e: e.health + e.shield))
                return True
            escort = army_center or self.staging_point
            if unit.distance_to(escort) > 7:
                unit.move(escort)
            return True

        if unit.type_id == UnitTypeId.BATTLECRUISER:
            local = enemies.closer_than(10, unit)
            if (
                unit.energy >= 100
                and local
                and self._can_cast_again(unit, AbilityId.YAMATO_YAMATOGUN, 6.0)
            ):
                unit(AbilityId.YAMATO_YAMATOGUN, max(local, key=lambda e: e.health + e.shield))
                return True
            return False

        if unit.type_id == UnitTypeId.CYCLONE:
            local = enemies.closer_than(7, unit).filter(lambda e: e.is_armored or e.is_flying)
            if local and self._can_cast_again(unit, AbilityId.LOCKON_LOCKON, 5.0):
                unit(AbilityId.LOCKON_LOCKON, max(local, key=lambda e: e.health + e.shield))
                return True
            return False

        if unit.type_id == UnitTypeId.WIDOWMINE:
            ground = enemies.not_flying.closer_than(7, unit)
            if ground.amount >= 2 and self._can_cast_again(unit, AbilityId.BURROWDOWN_WIDOWMINE, 2.0):
                unit(AbilityId.BURROWDOWN_WIDOWMINE)
                return True
            return False

        if unit.type_id == UnitTypeId.WIDOWMINEBURROWED:
            if (
                not enemies.closer_than(11, unit)
                and unit.distance_to(target_point) > 12
                and self._can_cast_again(unit, AbilityId.BURROWUP_WIDOWMINE, 3.0)
            ):
                unit(AbilityId.BURROWUP_WIDOWMINE)
            return True

        if unit.type_id == UnitTypeId.LIBERATOR:
            ground = enemies.not_flying.closer_than(10, unit)
            anchor = self._cluster_anchor(ground, 3.0, 3)
            if anchor is not None and self._can_cast_again(unit, AbilityId.MORPH_LIBERATORAGMODE, 5.0):
                unit(AbilityId.MORPH_LIBERATORAGMODE, anchor.position)
                return True
            return False

        if unit.type_id == UnitTypeId.LIBERATORAG:
            if (
                not enemies.not_flying.closer_than(13, unit)
                and unit.distance_to(target_point) > 15
                and self._can_cast_again(unit, AbilityId.MORPH_LIBERATORAAMODE, 5.0)
            ):
                unit(AbilityId.MORPH_LIBERATORAAMODE)
            return True

        if unit.type_id == UnitTypeId.HELLION:
            light = enemies.not_flying.closer_than(5, unit).filter(lambda e: e.is_light)
            if (
                light.amount >= 3
                and self.structures(UnitTypeId.ARMORY).ready
                and self._can_cast_again(unit, AbilityId.MORPH_HELLBAT, 5.0)
            ):
                unit(AbilityId.MORPH_HELLBAT)
                return True

        if unit.type_id == UnitTypeId.BANSHEE:
            anti_air = enemies.filter(lambda e: e.can_attack_air and e.distance_to(unit) < e.air_range + 2)
            cloak_done = self.already_pending_upgrade(UpgradeId.BANSHEECLOAK) == 1
            if (
                cloak_done
                and anti_air
                and unit.energy >= 50
                and not unit.has_buff(BuffId.BANSHEECLOAK)
                and self._can_cast_again(unit, AbilityId.BEHAVIOR_CLOAKON_BANSHEE, 4.0)
            ):
                unit(AbilityId.BEHAVIOR_CLOAKON_BANSHEE)
                return True
            if (
                unit.has_buff(BuffId.BANSHEECLOAK)
                and not anti_air
                and unit.energy < 25
                and self._can_cast_again(unit, AbilityId.BEHAVIOR_CLOAKOFF_BANSHEE, 4.0)
            ):
                unit(AbilityId.BEHAVIOR_CLOAKOFF_BANSHEE)
                return True
        return False

    def _micro_bio(self, unit, target_point, aggressive):
        if self._dodge(unit):
            return
        enemies = self._cached_enemies
        if self._preserve_hurt(unit, enemies):
            return

        # Never stand next to a baneling.
        if self._cached_banelings:
            close_banes = self._cached_banelings.closer_than(3.5, unit)
            if close_banes:
                self._flee(unit, close_banes.closest_to(unit).position, 3.0)
                return

        # Stim when a real fight is on.
        if (
            self._stim_ready
            and unit.type_id in STIM_ABILITY
            and enemies
            and not unit.has_buff(BuffId.STIMPACK)
            and not unit.has_buff(BuffId.STIMPACKMARAUDER)
        ):
            closest = enemies.closest_to(unit)
            threshold = 25 if unit.type_id == UnitTypeId.MARINE else 45
            if closest.distance_to(unit) < 8 and unit.health > threshold:
                unit(STIM_ABILITY[unit.type_id])
                return

        if unit.weapon_cooldown == 0:
            if enemies:
                best = self._best_target(unit, enemies)
                if best is not None:
                    unit.attack(best)
                    return
            structures_in_range = self.enemy_structures.filter(lambda s: unit.target_in_range(s))
            if structures_in_range:
                def struct_priority(s):
                    return (0 if s.type_id in DEFENSIVE_STRUCTS else 1, s.health + s.shield)
                unit.attack(min(structures_in_range, key=struct_priority))
                return
            self._ordered_attack_point(unit, target_point)
            return

        # Weapon on cooldown: range-aware stutter step.
        if enemies:
            threats = enemies.filter(
                lambda e: (e.can_attack_ground or e.type_id in SPECIAL_TARGETS)
                and e.distance_to(unit) < self._threat_range(e, unit) + 2.5
            )
            if threats:
                threat = threats.closest_to(unit)
                their_range = self._threat_range(threat, unit)
                my_range = unit.air_range if threat.is_flying else unit.ground_range
                if their_range < 1.0 or my_range > their_range + 0.1:
                    # We outrange them (or they are melee): kite backwards.
                    self._flee(unit, threat.position, 2.0)
                    return
                # They match/outrange us: stay on top of them, don't run.
                if unit.distance_to(threat) > my_range:
                    unit.move(threat.position)
                return
        # Splash on the field: don't clump while repositioning.
        if self._splash_threat and self._cached_bio_for_spread:
            crowd = self._cached_bio_for_spread.filter(
                lambda a: a.tag != unit.tag and a.distance_to(unit) < 1.3
            )
            if crowd:
                self._flee(unit, crowd.closest_to(unit).position, 1.5)
                return
        self._ordered_attack_point(unit, target_point)

    def _micro_medivac(self, unit, bio):
        if self._dodge(unit):
            return
        if unit.health_percentage < 0.35:
            unit.move(self.staging_point)
            return
        if not bio:
            unit.move(self.staging_point)
            return
        injured = bio.filter(lambda b: b.health_percentage < 0.99)
        if injured:
            dest = injured.closest_to(unit).position
        else:
            dest = bio.center
        if unit.distance_to(dest) > 9 and not unit.has_buff(BuffId.MEDIVACSPEEDBOOST):
            unit(AbilityId.EFFECT_MEDIVACIGNITEAFTERBURNERS)
        unit.move(dest)

    def _micro_tank(self, unit, target_point, bio_center):
        enemies = self._cached_enemies
        ground_enemies = enemies.not_flying
        near_units = ground_enemies.filter(lambda e: 4 < e.distance_to(unit) < 13)
        very_close = ground_enemies.filter(lambda e: e.distance_to(unit) <= 4)
        near_structures = self.enemy_structures.filter(lambda s: s.distance_to(unit) < 11)

        if unit.type_id == UnitTypeId.SIEGETANK:
            if self._dodge(unit):
                return
            if (near_units.amount >= 2 and not very_close) or (near_structures and self.attack_mode):
                unit(AbilityId.SIEGEMODE_SIEGEMODE)
                self._tank_last_enemy[unit.tag] = self.time
            elif bio_center is not None and not near_units and unit.distance_to(bio_center) > 6:
                # Stay tucked behind the bio ball.
                unit.move(bio_center.towards(unit.position, 2))
            else:
                self._ordered_attack_point(unit, target_point)
        else:  # sieged
            far_units = ground_enemies.filter(lambda e: e.distance_to(unit) < 16)
            if far_units or near_structures:
                self._tank_last_enemy[unit.tag] = self.time
            elif self.time - self._tank_last_enemy.get(unit.tag, 0) > 5:
                unit(AbilityId.UNSIEGE_UNSIEGE)

    def _micro_viking(self, unit, target_point, bio_center):
        if self._dodge(unit):
            return
        air = self._cached_air_enemies
        if air:
            if unit.weapon_cooldown == 0:
                in_range = air.filter(lambda e: unit.target_in_range(e))
                if in_range:
                    unit.attack(min(in_range, key=lambda e: e.health + e.shield))
                    return
                unit.attack(air.closest_to(unit).position)
                return
            threats = air.filter(
                lambda e: e.can_attack_air and e.distance_to(unit) < e.air_range + 2.0
            )
            if threats:
                threat = threats.closest_to(unit)
                if unit.air_range > threat.air_range + 0.1:
                    self._flee(unit, threat.position, 2.0)
                    return
            return
        if bio_center is not None and unit.distance_to(bio_center) > 9:
            unit.move(bio_center)
        else:
            self._ordered_attack_point(unit, target_point)
