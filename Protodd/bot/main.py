"""
Protodd - adaptive Protoss bot for the aiarena.net ladder.
Same architecture as Battencruiser: per-opponent build/timing bandits,
opponent fingerprinting, fight-or-flee power evaluation, effect dodging,
range-aware stutter micro, splash spreading, blink escapes.

Builds:
  * four_gate        - warpgate rush with proxy pylon warp-ins (~4:45 push)
  * stalker_immortal - expand into stalker/immortal with blink + upgrades
  * proxy_gates      - 2 proxy gateways zealot all-in with probe pull
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

MELEE_TYPES = {UnitTypeId.ZEALOT, UnitTypeId.DARKTEMPLAR, UnitTypeId.ARCHON}
ARMY_TYPES = MELEE_TYPES | {
    UnitTypeId.ADEPT, UnitTypeId.STALKER, UnitTypeId.SENTRY,
    UnitTypeId.HIGHTEMPLAR, UnitTypeId.IMMORTAL, UnitTypeId.COLOSSUS,
    UnitTypeId.DISRUPTOR, UnitTypeId.WARPPRISM, UnitTypeId.PHOENIX,
    UnitTypeId.VOIDRAY, UnitTypeId.ORACLE, UnitTypeId.TEMPEST,
    UnitTypeId.CARRIER, UnitTypeId.MOTHERSHIP,
}
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
SPECIAL_TARGETS = {
    UnitTypeId.BANELING, UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED,
    UnitTypeId.INFESTOR, UnitTypeId.HIGHTEMPLAR, UnitTypeId.DISRUPTOR,
    UnitTypeId.ORACLE, UnitTypeId.LURKERMPBURROWED, UnitTypeId.SIEGETANKSIEGED,
    UnitTypeId.RAVAGER, UnitTypeId.VIPER,
}
AIR_THREAT_TYPES = {
    UnitTypeId.BATTLECRUISER, UnitTypeId.CARRIER, UnitTypeId.VOIDRAY,
    UnitTypeId.MUTALISK, UnitTypeId.PHOENIX, UnitTypeId.TEMPEST,
    UnitTypeId.BROODLORD, UnitTypeId.LIBERATOR, UnitTypeId.LIBERATORAG,
    UnitTypeId.BANSHEE, UnitTypeId.CORRUPTOR,
}
CASTER_NOMINAL_DPS = {
    UnitTypeId.INFESTOR: 14, UnitTypeId.HIGHTEMPLAR: 14, UnitTypeId.DISRUPTOR: 14,
    UnitTypeId.VIPER: 12, UnitTypeId.ORACLE: 15, UnitTypeId.RAVEN: 8,
    UnitTypeId.WIDOWMINE: 12, UnitTypeId.WIDOWMINEBURROWED: 16,
    UnitTypeId.BANELING: 16, UnitTypeId.LURKERMP: 10, UnitTypeId.MEDIVAC: 10,
}
STATIC_DEFENSE_DPS = {
    UnitTypeId.PHOTONCANNON: 22, UnitTypeId.SPINECRAWLER: 22,
    UnitTypeId.SPORECRAWLER: 18, UnitTypeId.BUNKER: 28,
    UnitTypeId.PLANETARYFORTRESS: 35, UnitTypeId.MISSILETURRET: 25,
}
DODGE_EFFECTS = {
    EffectId.PSISTORMPERSISTENT: 1.5,
    EffectId.RAVAGERCORROSIVEBILECP: 1.2,
    EffectId.NUKEPERSISTENT: 2.0,
    EffectId.LURKERMP: 1.0,
    EffectId.BLINDINGCLOUDCP: 1.0,
    EffectId.LIBERATORTARGETMORPHDELAYPERSISTENT: 1.0,
    EffectId.LIBERATORTARGETMORPHPERSISTENT: 1.0,
}


class ProtoddBot(BotAI):
    NAME = "Protodd"
    RACE_NAME = "Protoss"

    def __init__(self):
        self.raw_affects_selection = True
        self.strategy = None
        self.attack_mode = False
        self.enemy_rush_detected = False
        self.worker_rush_active = False
        self.cloak_threat = False
        self.greeted = False
        self.enemy_main_visited = False
        self.enemy_natural_visited = False
        self.cannon_targets = None
        self._base_threats = None
        self._retreat_until = 0.0
        self._max_air_threat = 0
        self._rush_seen_live = False
        self._cloak_seen_live = False
        self._air_seen_live = 0
        self._armored_seen_max = 0
        self._light_seen_max = 0
        self._splash_threat = False
        self._enemy_base_count = 0
        self._proxy_alert = False
        self._all_in_suspected = False
        self._enemy_tech_seen = False
        self._first_pressure_recorded = False
        self._point_order_cache = {}
        self._special_last_cast = {}
        self._reported_errors = set()
        self._stats = {}
        self._retreat_count = 0
        self._focus_board = {}
        self._recorder = GameRecorder('Protodd', 'protodd')
        self._cached_enemies = None
        self._cached_army_for_spread = None
        self._dodge_zones = []
        self._blink_escaped = set()
        self.natural_position = None
        self.enemy_natural = None
        self.staging_point = None
        self.proxy_point = None
        self.proxy_scv_tags = []
        self.scout_sent = False
        self.scout_tag = None
        self.probes_pulled = False

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

        try:
            if self.strategy:
                if self.strategy.expects("rushed") or self.strategy.expects("worker_rush"):
                    self.enemy_rush_detected = True
                try:
                    fp = float(self.strategy.profile.get("first_pressure_ewma", 9999))
                    samples = int(self.strategy.profile.get("pressure_samples", 0))
                    if samples >= 3 and fp < 360:
                        self.enemy_rush_detected = True  # their pressure comes early
                except Exception:
                    pass
                if self.strategy.expects("cloak"):
                    self.cloak_threat = True
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
        self.staging_point = self.natural_position.towards(self.game_info.map_center, 7)
        center = self.game_info.map_center
        self.proxy_point = center.towards(enemy_start, center.distance_to(enemy_start) * 0.45)

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
        return "proxy_gates" if self._proxying else "macro"

    @property
    def all_in(self):
        return self._proxying or self.supply_used > 190 or not self.townhalls

    def _army_supply_est(self):
        return float(self.supply_army)

    def _base_build_config(self):
        t = self.time
        s = self.strategy
        gates0 = {"gate1": 1, "gate2": 2, "gate4": 4}.get(getattr(s, "gates_open", "gate2"), 2)
        gas0 = {"no_gas": 0, "one_gas": 1, "two_gas": 2}.get(getattr(s, "gas_open", "one_gas"), 1)
        bases = max(1, self.townhalls.amount)
        if self._proxying:
            return dict(
                probe_cap=17, gas_target=0, gate_cap=0,
                want_robo=False, want_twilight=False, want_forge=False,
                immortal_cap=0, observer_cap=0,
                attack_min=6, retreat_at=0,
                robo_cap=0, stargate_cap=0, want_robo_bay=False,
                want_stargate=False, want_fleet=False,
                want_templar=False, want_dark=False,
            )
        developed = bases >= 2 or t > 390
        if not developed:
            gate_cap = 1 if t < 100 else gates0
            probe_cap = 21 + gates0
            if gates0 >= 4 and t > 140:
                gas_target = max(gas0, 2)
            else:
                gas_target = gas0 if t < 145 else max(gas0, 1)
        else:
            gate_cap = 3 if bases == 2 else 6
            probe_cap = min(66, 22 * bases)
            gas_target = min(2 * bases, 6)
        attack_min = {1: 40, 2: 26, 4: 16}.get(gates0, 26)
        return dict(
            probe_cap=probe_cap, gas_target=gas_target, gate_cap=gate_cap,
            want_robo=(developed and t > 210),
            want_twilight=(developed and t > 300),
            want_forge=(developed and t > 320),
            immortal_cap=(4 if developed else 0),
            observer_cap=((2 if self.cloak_threat else 1) if developed else 0),
            attack_min=attack_min,
            retreat_at=max(4, attack_min * 2 // 5),
            robo_cap=(1 if developed else 0),
            stargate_cap=0,
            want_robo_bay=False,
            want_stargate=False,
            want_fleet=False,
            want_templar=False,
            want_dark=False,
        )

    def build_config(self):
        cfg = self._base_build_config()
        try:
            if self.strategy:
                cfg["attack_min"] = max(2, int(round(cfg["attack_min"] * self.strategy.aggression_mult)))
                cfg["probe_cap"] = max(12, int(round(cfg["probe_cap"] * self.strategy.greed_worker_mult)))
        except Exception:
            pass
        if (self.cloak_threat or self._max_air_threat >= 3) and self.active_build != "proxy_gates":
            cfg["want_forge"] = cfg["want_forge"] or self.time > 160
            cfg["observer_cap"] = max(cfg["observer_cap"], 1)
            if self._max_air_threat >= 6:
                cfg["want_stargate"] = cfg["want_stargate"] or self.time > 240
                cfg["stargate_cap"] = max(cfg["stargate_cap"], 2)
                cfg["want_fleet"] = cfg["want_fleet"] or self.time > 480
                cfg["gas_target"] = max(cfg["gas_target"], 5)
        # Learned tech focus.
        try:
            tech = self.strategy.tech if self.strategy else "blink_stalker"
            if self.active_build != "proxy_gates":
                if tech == "immortal_core":
                    cfg["immortal_cap"] = max(cfg["immortal_cap"], 6)
                    cfg["want_robo"] = cfg["want_robo"] or self.time > 200
                elif tech == "chargelot":
                    cfg["want_twilight"] = cfg["want_twilight"] or self.time > 240
                army = getattr(self.strategy, "army", "mixed")
                if army == "gateway":
                    cfg["gate_cap"] += 2
                    cfg["want_templar"] = self.time > 420
                    cfg["want_dark"] = self.time > 520
                elif army == "robotics":
                    cfg["robo_cap"] = 3
                    cfg["want_robo"] = self.time > 180
                    cfg["want_robo_bay"] = self.time > 360
                    cfg["immortal_cap"] = 10
                    cfg["gas_target"] = max(cfg["gas_target"], 5)
                elif army == "sky":
                    cfg["stargate_cap"] = 3
                    cfg["want_stargate"] = self.time > 180
                    cfg["want_fleet"] = self.time > 450
                    cfg["gas_target"] = max(cfg["gas_target"], 6)
                else:
                    cfg["robo_cap"] = max(2, cfg["robo_cap"])
                    cfg["stargate_cap"] = 2
                    cfg["want_robo"] = self.time > 200
                    cfg["want_stargate"] = self.time > 260
                    cfg["want_robo_bay"] = self.time > 480
                    cfg["want_fleet"] = self.time > 600
                    cfg["want_templar"] = self.time > 500
                    cfg["want_dark"] = self.time > 650
                    cfg["gas_target"] = max(cfg["gas_target"], 6)
        except Exception:
            pass
        return cfg

    # ------------------------------------------------------------------ frame

    async def on_step(self, iteration: int):
        try:
            await self._step(iteration)
        except Exception as exc:
            self._report_error("on_step", exc)

    async def _step(self, iteration: int):
        if not self.greeted and iteration >= 2:
            self.greeted = True
            try:
                await self.chat_send("(glhf)")
            except Exception:
                pass

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

        if not self.townhalls:
            await self._safe(self._desperado(iteration))
            return

        cfg = self.build_config()
        await self._safe(self.manage_pylons(cfg))
        await self._safe(self.manage_probes(cfg))
        await self._safe(self.manage_chrono())
        await self._safe(self.manage_gas(cfg))
        await self._safe(self.manage_expansion(cfg))
        await self._safe(self.manage_structures(cfg))
        await self._safe(self.manage_proxy(cfg))
        await self._safe(self.manage_warp_and_train(cfg))
        await self._safe(self.manage_upgrades(cfg))
        await self._safe(self.manage_scout())
        await self._safe(self.manage_defense())
        await self._safe(self.manage_worker_safety())
        if iteration % 2 == 0:
            await self._safe(self.control_army(cfg))
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
            enemy_bases = self.enemy_structures.of_type(ENEMY_TOWNHALLS)
            pools = self.enemy_structures(UnitTypeId.SPAWNINGPOOL)
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

        air_now = enemies.filter(lambda u: u.is_flying and u.type_id in AIR_THREAT_TYPES).amount
        if air_now > self._max_air_threat:
            self._max_air_threat = air_now
        if air_now > self._air_seen_live:
            self._air_seen_live = air_now

        ground_combat = enemies.filter(
            lambda u: not u.is_flying and u.type_id not in WORKER_TYPES and u.type_id not in IGNORE_TARGETS
        )
        armored = ground_combat.filter(lambda u: u.is_armored).amount
        light = ground_combat.filter(lambda u: u.is_light).amount
        if armored > self._armored_seen_max:
            self._armored_seen_max = armored
        if light > self._light_seen_max:
            self._light_seen_max = light
        if not self._splash_threat and enemies.filter(
            lambda u: u.type_id in {
                UnitTypeId.BANELING, UnitTypeId.SIEGETANKSIEGED, UnitTypeId.LURKERMP,
                UnitTypeId.LURKERMPBURROWED, UnitTypeId.DISRUPTOR, UnitTypeId.HIGHTEMPLAR,
                UnitTypeId.INFESTOR, UnitTypeId.WIDOWMINE, UnitTypeId.WIDOWMINEBURROWED,
            }
        ):
            self._splash_threat = True

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

        self._scout_intel()

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

    def _power_pylon(self):
        pylons = self.structures(UnitTypeId.PYLON).ready
        if not pylons:
            return None
        return pylons.closest_to(self.start_location.towards(self.game_info.map_center, 8))

    async def manage_pylons(self, cfg):
        if self.supply_cap >= 200:
            return
        if self.supply_used < 13:
            return
        production = (
            self.structures(UnitTypeId.GATEWAY).ready.amount
            + self.structures(UnitTypeId.WARPGATE).ready.amount * 2
            + self.structures(UnitTypeId.ROBOTICSFACILITY).ready.amount
        )
        threshold = min(18, 3 + 2 * max(1, production))
        pending_cap = 2 if (self.minerals > 500 and self.supply_cap > 60) else 1
        if self.supply_left >= threshold:
            return
        if self.already_pending(UnitTypeId.PYLON) >= pending_cap:
            return
        if not self.can_afford(UnitTypeId.PYLON):
            return
        near = self.townhalls.first.position.towards(self.game_info.map_center, 7)
        await self.build(UnitTypeId.PYLON, near=near)

    async def manage_probes(self, cfg):
        if self.supply_left <= 0:
            return
        if self.supply_workers + self.already_pending(UnitTypeId.PROBE) >= cfg["probe_cap"]:
            return
        for nexus in self.townhalls.ready.idle:
            if self.can_afford(UnitTypeId.PROBE):
                nexus.train(UnitTypeId.PROBE)

    async def manage_chrono(self):
        for nexus in self.townhalls.ready:
            if nexus.energy < 50:
                continue
            target = None
            cores = self.structures(UnitTypeId.CYBERNETICSCORE).ready
            robos = self.structures(UnitTypeId.ROBOTICSFACILITY).ready
            gates = self.structures(UnitTypeId.GATEWAY).ready
            for group in (cores, robos, gates):
                busy = group.filter(lambda s: not s.is_idle and not s.has_buff(BuffId.CHRONOBOOSTENERGYCOST))
                if busy:
                    target = busy.first
                    break
            if target is None and not nexus.is_idle and not nexus.has_buff(BuffId.CHRONOBOOSTENERGYCOST):
                target = nexus
            if target is not None:
                nexus(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, target)

    async def manage_gas(self, cfg):
        if cfg["gas_target"] <= 0:
            return
        if not (self.structures(UnitTypeId.GATEWAY) or self.already_pending(UnitTypeId.GATEWAY)):
            return
        current = self.gas_buildings.amount + self.already_pending(UnitTypeId.ASSIMILATOR)
        if current >= cfg["gas_target"] or not self.can_afford(UnitTypeId.ASSIMILATOR):
            return
        for nexus in self.townhalls.ready:
            for vg in self.vespene_geyser.closer_than(10, nexus):
                if self.gas_buildings.filter(lambda u: u.distance_to(vg) < 1):
                    continue
                worker = self.select_build_worker(vg.position)
                if worker is not None:
                    worker.build_gas(vg)
                    return

    def _wants_expand(self):
        t = self.time + (self.strategy.greed_expand_shift if self.strategy else 0)
        bases = self.townhalls.amount
        if self._proxying or self._base_threats:
            return False
        # Failed pushes: stop banging heads, take a base and grow instead.
        if self._retreat_count >= 2 and bases == 1 and t > 240:
            return True
        gates_arm = getattr(self.strategy, "gates_open", "gate2") if self.strategy else "gate2"
        if bases == 1:
            first = {"gate1": 155, "gate2": 240, "gate4": 390}.get(gates_arm, 240)
            if self.enemy_rush_detected:
                first = max(first, 240)
            return t > first
        if bases == 2:
            return t > 390
        return self.minerals > 500 and t > 540

    async def manage_expansion(self, cfg):
        if not self._wants_expand():
            return
        if self.already_pending(UnitTypeId.NEXUS) or not self.can_afford(UnitTypeId.NEXUS):
            return
        location = await self.get_next_expansion()
        if location is None:
            return
        if self.enemy_units.filter(lambda u: u.distance_to(location) < 12).amount >= 2:
            return
        worker = self.select_build_worker(location)
        if worker is not None and self.can_afford(UnitTypeId.NEXUS):
            worker.build(UnitTypeId.NEXUS, location)

    async def _build_near(self, type_id, near, step=4):
        location = await self.find_placement(type_id, near, placement_step=step)
        if location is None:
            return False
        worker = self.select_build_worker(location)
        if worker is None:
            return False
        worker.build(type_id, location)
        return True

    async def manage_structures(self, cfg):
        if self.active_build == "proxy_gates" and self.time < 420:
            return
        t = self.time
        pylon = self._power_pylon()
        if pylon is None:
            return
        anchor = pylon.position

        gates = (
            self.structures(UnitTypeId.GATEWAY).amount
            + self.structures(UnitTypeId.WARPGATE).amount
            + self.already_pending(UnitTypeId.GATEWAY)
        )
        if gates < cfg["gate_cap"] and self.can_afford(UnitTypeId.GATEWAY) and self.supply_used >= 14:
            await self._build_near(UnitTypeId.GATEWAY, anchor, step=3)

        if gates >= 1:
            cores = self.structures(UnitTypeId.CYBERNETICSCORE).amount + self.already_pending(UnitTypeId.CYBERNETICSCORE)
            if (
                cores == 0
                and self.structures(UnitTypeId.GATEWAY).ready
                and self.can_afford(UnitTypeId.CYBERNETICSCORE)
            ):
                await self._build_near(UnitTypeId.CYBERNETICSCORE, anchor, step=3)

        core_ready = bool(self.structures(UnitTypeId.CYBERNETICSCORE).ready)

        if cfg["want_robo"] and core_ready:
            robos = self.structures(UnitTypeId.ROBOTICSFACILITY).amount + self.already_pending(UnitTypeId.ROBOTICSFACILITY)
            if robos < cfg["robo_cap"] and self.can_afford(UnitTypeId.ROBOTICSFACILITY):
                await self._build_near(UnitTypeId.ROBOTICSFACILITY, anchor, step=3)

        if cfg["want_twilight"] and core_ready:
            tc = self.structures(UnitTypeId.TWILIGHTCOUNCIL).amount + self.already_pending(UnitTypeId.TWILIGHTCOUNCIL)
            if tc == 0 and self.can_afford(UnitTypeId.TWILIGHTCOUNCIL):
                await self._build_near(UnitTypeId.TWILIGHTCOUNCIL, anchor, step=3)

        if cfg["want_stargate"] and core_ready:
            total = self.structures(UnitTypeId.STARGATE).amount + self.already_pending(UnitTypeId.STARGATE)
            if total < cfg["stargate_cap"] and self.can_afford(UnitTypeId.STARGATE):
                await self._build_near(UnitTypeId.STARGATE, anchor, step=3)

        if cfg["want_robo_bay"] and self.structures(UnitTypeId.ROBOTICSFACILITY).ready:
            total = self.structures(UnitTypeId.ROBOTICSBAY).amount + self.already_pending(UnitTypeId.ROBOTICSBAY)
            if total == 0 and self.can_afford(UnitTypeId.ROBOTICSBAY):
                await self._build_near(UnitTypeId.ROBOTICSBAY, anchor, step=3)

        if cfg["want_fleet"] and self.structures(UnitTypeId.STARGATE).ready:
            total = self.structures(UnitTypeId.FLEETBEACON).amount + self.already_pending(UnitTypeId.FLEETBEACON)
            if total == 0 and self.can_afford(UnitTypeId.FLEETBEACON):
                await self._build_near(UnitTypeId.FLEETBEACON, anchor, step=3)

        if cfg["want_templar"] and self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready:
            total = self.structures(UnitTypeId.TEMPLARARCHIVE).amount + self.already_pending(UnitTypeId.TEMPLARARCHIVE)
            if total == 0 and self.can_afford(UnitTypeId.TEMPLARARCHIVE):
                await self._build_near(UnitTypeId.TEMPLARARCHIVE, anchor, step=3)

        if cfg["want_dark"] and self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready:
            total = self.structures(UnitTypeId.DARKSHRINE).amount + self.already_pending(UnitTypeId.DARKSHRINE)
            if total == 0 and self.can_afford(UnitTypeId.DARKSHRINE):
                await self._build_near(UnitTypeId.DARKSHRINE, anchor, step=3)

        if cfg["want_forge"]:
            forges = self.structures(UnitTypeId.FORGE).amount + self.already_pending(UnitTypeId.FORGE)
            if forges == 0 and self.can_afford(UnitTypeId.FORGE):
                await self._build_near(UnitTypeId.FORGE, anchor, step=3)

        # Cannons at mineral lines vs cloak/air; battery at the natural vs rushes.
        if self.structures(UnitTypeId.FORGE).ready and (self.cloak_threat or self._max_air_threat >= 3):
            if self.can_afford(UnitTypeId.PHOTONCANNON):
                needed = 2 if self._max_air_threat >= 5 else 1
                for th in self.townhalls.ready:
                    if self.structures(UnitTypeId.PHOTONCANNON).closer_than(9, th).amount >= needed:
                        continue
                    if not self.structures(UnitTypeId.PYLON).ready.closer_than(7, th):
                        await self._build_near(UnitTypeId.PYLON, th.position.towards(self.game_info.map_center, 4), step=2)
                        break
                    mfs = self.mineral_field.closer_than(10, th)
                    near = mfs.center.towards(th.position, 2) if mfs else th.position
                    if await self._build_near(UnitTypeId.PHOTONCANNON, near, step=2):
                        break

        if self.enemy_rush_detected and t < 420 and core_ready:
            nat_nexus = self.townhalls.filter(lambda x: x.distance_to(self.natural_position) < 8)
            anchor_pos = self.natural_position if nat_nexus else self.townhalls.first.position
            if not self.structures(UnitTypeId.PYLON).ready.closer_than(7, anchor_pos):
                if self.can_afford(UnitTypeId.PYLON):
                    await self._build_near(UnitTypeId.PYLON, anchor_pos.towards(self.game_info.map_center, 4), step=2)
            else:
                batteries = self.structures(UnitTypeId.SHIELDBATTERY).amount + self.already_pending(UnitTypeId.SHIELDBATTERY)
                if batteries < 2 and self.can_afford(UnitTypeId.SHIELDBATTERY):
                    await self._build_near(UnitTypeId.SHIELDBATTERY, anchor_pos.towards(self.game_info.map_center, 3), step=2)

    async def manage_proxy(self, cfg):
        t = self.time
        # Forward pylon for warp-ins on gate-heavy home openings.
        if (
            not self._proxying
            and getattr(self.strategy, "gates_open", "") == "gate4"
            and 200 < t < 400
        ):
            proxy_pylons = self.structures(UnitTypeId.PYLON).filter(
                lambda p: p.distance_to(self.proxy_point) < 20
            )
            if not proxy_pylons and self.already_pending(UnitTypeId.PYLON) < 2 and self.can_afford(UnitTypeId.PYLON):
                worker = self.select_build_worker(self.proxy_point)
                if worker is not None:
                    location = await self.find_placement(UnitTypeId.PYLON, self.proxy_point, placement_step=3)
                    if location is not None:
                        worker.build(UnitTypeId.PYLON, location)
            return

        if not self._proxying:
            return
        alive = self.workers.tags
        self.proxy_scv_tags = [tag for tag in self.proxy_scv_tags if tag in alive]
        if t > 15 and len(self.proxy_scv_tags) < 2 and self.workers.amount >= 12:
            candidates = self.workers.gathering.sorted(lambda w: w.distance_to(self.proxy_point))
            for worker in candidates:
                if len(self.proxy_scv_tags) >= 2:
                    break
                if worker.tag in self.proxy_scv_tags:
                    continue
                self.proxy_scv_tags.append(worker.tag)
                worker.move(self.proxy_point)

        proxy_probes = self.workers.tags_in(self.proxy_scv_tags)
        proxy_pylons = self.structures(UnitTypeId.PYLON).filter(lambda p: p.distance_to(self.proxy_point) < 18)
        if not proxy_pylons and self.already_pending(UnitTypeId.PYLON) == 0 and self.can_afford(UnitTypeId.PYLON):
            for worker in proxy_probes:
                if worker.distance_to(self.proxy_point) < 12:
                    location = await self.find_placement(UnitTypeId.PYLON, self.proxy_point, placement_step=3)
                    if location is not None:
                        worker.build(UnitTypeId.PYLON, location)
                        break
        elif proxy_pylons.ready:
            gates = self.structures(UnitTypeId.GATEWAY).amount + self.already_pending(UnitTypeId.GATEWAY)
            cap = 2 if self.minerals < 400 else 3
            if gates < cap and self.can_afford(UnitTypeId.GATEWAY):
                for worker in proxy_probes:
                    if worker.distance_to(self.proxy_point) < 14:
                        location = await self.find_placement(
                            UnitTypeId.GATEWAY, proxy_pylons.ready.first.position, placement_step=3
                        )
                        if location is not None:
                            worker.build(UnitTypeId.GATEWAY, location)
                            break
        for worker in proxy_probes:
            if worker.is_idle:
                worker.move(self.proxy_point)

    async def manage_warp_and_train(self, cfg):
        wg_done = self.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) == 1
        army_plan = getattr(self.strategy, "army", "mixed") if self.strategy else "mixed"

        # Morph gateways once warpgate tech is done.
        if wg_done:
            for gate in self.structures(UnitTypeId.GATEWAY).ready.idle:
                gate(AbilityId.MORPH_WARPGATE)

        core_ready = bool(self.structures(UnitTypeId.CYBERNETICSCORE).ready)

        gateway_units = [UnitTypeId.ZEALOT]
        if core_ready:
            gateway_units += [UnitTypeId.STALKER, UnitTypeId.ADEPT, UnitTypeId.SENTRY]
        if self.structures(UnitTypeId.TEMPLARARCHIVE).ready:
            gateway_units.append(UnitTypeId.HIGHTEMPLAR)
        if self.structures(UnitTypeId.DARKSHRINE).ready:
            gateway_units.append(UnitTypeId.DARKTEMPLAR)
        if army_plan not in {"gateway", "mixed"}:
            gateway_units = [u for u in gateway_units if u in {UnitTypeId.ZEALOT, UnitTypeId.STALKER, UnitTypeId.SENTRY}]
        gateway_units.sort(key=lambda u: self.units(u).amount + self.already_pending(u))
        stalker_target = max(6, 2 * self._max_air_threat)
        if (
            UnitTypeId.STALKER in gateway_units
            and self._max_air_threat >= 3
            and self.units(UnitTypeId.STALKER).amount + self.already_pending(UnitTypeId.STALKER) < stalker_target
        ):
            gateway_units.remove(UnitTypeId.STALKER)
            gateway_units.insert(0, UnitTypeId.STALKER)
        warp_abilities = {
            UnitTypeId.ZEALOT: AbilityId.WARPGATETRAIN_ZEALOT,
            UnitTypeId.STALKER: AbilityId.WARPGATETRAIN_STALKER,
            UnitTypeId.ADEPT: AbilityId.TRAINWARP_ADEPT,
            UnitTypeId.SENTRY: AbilityId.WARPGATETRAIN_SENTRY,
            UnitTypeId.HIGHTEMPLAR: AbilityId.WARPGATETRAIN_HIGHTEMPLAR,
            UnitTypeId.DARKTEMPLAR: AbilityId.WARPGATETRAIN_DARKTEMPLAR,
        }

        # Warp-ins.
        warpgates = self.structures(UnitTypeId.WARPGATE).ready
        if warpgates and self.supply_left >= 2:
            # Forward pylon when attacking, home power otherwise.
            pylons = self.structures(UnitTypeId.PYLON).ready
            warp_anchor = None
            if pylons:
                if self.attack_mode:
                    warp_anchor = pylons.closest_to(self.enemy_start_locations[0])
                else:
                    warp_anchor = pylons.closest_to(self.staging_point)
            if warp_anchor is not None:
                abilities_list = await self.get_available_abilities(list(warpgates))
                for warpgate, abilities in zip(warpgates, abilities_list):
                    choice = next(
                        ((u, warp_abilities[u]) for u in gateway_units
                         if warp_abilities[u] in abilities and self.can_afford(u)),
                        None,
                    )
                    if choice is None:
                        continue
                    unit_type, warp_ability = choice
                    try:
                        pos = warp_anchor.position.to2.random_on_distance(4)
                    except Exception:
                        pos = warp_anchor.position
                    placement = await self.find_placement(warp_ability, pos, placement_step=1)
                    if placement is None:
                        continue
                    warpgate.warp_in(unit_type, placement)

        # Plain gateway production before warpgate tech.
        for gate in self.structures(UnitTypeId.GATEWAY).ready.idle:
            if wg_done:
                break
            if self.supply_left < 2:
                break
            choice = next((u for u in gateway_units if self.can_afford(u)), None)
            if choice is not None:
                gate.train(choice)

        # Robotics plans explore support, siege and heavy units.
        observers = self.units(UnitTypeId.OBSERVER).amount + self.already_pending(UnitTypeId.OBSERVER)
        for robo in self.structures(UnitTypeId.ROBOTICSFACILITY).ready.idle:
            if self.supply_left < 1:
                break
            if observers < cfg["observer_cap"] and self.can_afford(UnitTypeId.OBSERVER):
                robo.train(UnitTypeId.OBSERVER)
                observers += 1
                continue
            choices = [UnitTypeId.IMMORTAL, UnitTypeId.WARPPRISM]
            if self.structures(UnitTypeId.ROBOTICSBAY).ready:
                choices += [UnitTypeId.COLOSSUS, UnitTypeId.DISRUPTOR]
            choices.sort(key=lambda u: self.units(u).amount + self.already_pending(u))
            if self._armored_seen_max >= max(4, self._light_seen_max):
                choices.remove(UnitTypeId.IMMORTAL)
                choices.insert(0, UnitTypeId.IMMORTAL)
            elif self._light_seen_max >= 8 and UnitTypeId.COLOSSUS in choices:
                choices.remove(UnitTypeId.COLOSSUS)
                choices.insert(0, UnitTypeId.COLOSSUS)
            choice = next((u for u in choices if self.can_afford(u)), None)
            if choice is not None and (army_plan in {"robotics", "mixed"} or choice == UnitTypeId.WARPPRISM):
                robo.train(choice)

        # Stargate plans can progress from harassment through capital ships.
        for stargate in self.structures(UnitTypeId.STARGATE).ready.idle:
            if self.supply_left < 2:
                break
            choices = [UnitTypeId.PHOENIX, UnitTypeId.ORACLE, UnitTypeId.VOIDRAY]
            if self.structures(UnitTypeId.FLEETBEACON).ready:
                choices += [UnitTypeId.TEMPEST, UnitTypeId.CARRIER]
            choices.sort(key=lambda u: self.units(u).amount + self.already_pending(u))
            if self._max_air_threat >= 3:
                preferred = (
                    UnitTypeId.TEMPEST
                    if UnitTypeId.TEMPEST in choices and self._max_air_threat >= 8
                    else UnitTypeId.PHOENIX
                )
                choices.remove(preferred)
                choices.insert(0, preferred)
            elif self._armored_seen_max >= max(4, self._light_seen_max):
                choices.remove(UnitTypeId.VOIDRAY)
                choices.insert(0, UnitTypeId.VOIDRAY)
            choice = next((u for u in choices if self.can_afford(u)), None)
            if choice is not None and army_plan in {"sky", "mixed"}:
                stargate.train(choice)

        if self.structures(UnitTypeId.FLEETBEACON).ready and not self.units(UnitTypeId.MOTHERSHIP):
            for nexus in self.townhalls(UnitTypeId.NEXUS).ready.idle:
                if self.can_afford(UnitTypeId.MOTHERSHIP):
                    nexus.train(UnitTypeId.MOTHERSHIP)
                    break

        templars = self.units.of_type({UnitTypeId.HIGHTEMPLAR, UnitTypeId.DARKTEMPLAR}).idle
        if templars.amount >= 2 and self.units(UnitTypeId.ARCHON).amount < 3:
            for templar in templars.take(2):
                templar(AbilityId.MORPH_ARCHON)

    async def manage_upgrades(self, cfg):
        if self.active_build == "proxy_gates" and self.time < 420:
            return
        # Warpgate research first.
        for core in self.structures(UnitTypeId.CYBERNETICSCORE).ready.idle:
            if (
                self.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) == 0
                and self.can_afford(UpgradeId.WARPGATERESEARCH)
            ):
                core.research(UpgradeId.WARPGATERESEARCH)

        first, second = UpgradeId.BLINKTECH, UpgradeId.CHARGE
        if self.strategy and self.strategy.tech == "chargelot":
            first, second = UpgradeId.CHARGE, UpgradeId.BLINKTECH
        for tc in self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready.idle:
            if self.already_pending_upgrade(first) == 0 and self.can_afford(first):
                tc.research(first)
            elif (
                self.already_pending_upgrade(first) == 1
                and self.already_pending_upgrade(second) == 0
                and self.can_afford(second)
            ):
                tc.research(second)

        twilight_ready = bool(self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready)
        ground_upgrades = [
            (UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1, False),
            (UpgradeId.PROTOSSGROUNDARMORSLEVEL1, False),
            (UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2, True),
            (UpgradeId.PROTOSSGROUNDARMORSLEVEL2, True),
            (UpgradeId.PROTOSSGROUNDWEAPONSLEVEL3, True),
            (UpgradeId.PROTOSSGROUNDARMORSLEVEL3, True),
            (UpgradeId.PROTOSSSHIELDSLEVEL1, False),
            (UpgradeId.PROTOSSSHIELDSLEVEL2, True),
            (UpgradeId.PROTOSSSHIELDSLEVEL3, True),
        ]
        for forge in self.structures(UnitTypeId.FORGE).ready.idle:
            for upgrade, needs_tc in ground_upgrades:
                if needs_tc and not twilight_ready:
                    continue
                if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                    forge.research(upgrade)
                    break

        if self.structures(UnitTypeId.STARGATE):
            air_upgrades = [
                UpgradeId.PROTOSSAIRWEAPONSLEVEL1,
                UpgradeId.PROTOSSAIRARMORSLEVEL1,
                UpgradeId.PROTOSSAIRWEAPONSLEVEL2,
                UpgradeId.PROTOSSAIRARMORSLEVEL2,
                UpgradeId.PROTOSSAIRWEAPONSLEVEL3,
                UpgradeId.PROTOSSAIRARMORSLEVEL3,
            ]
            for core in self.structures(UnitTypeId.CYBERNETICSCORE).ready.idle:
                for upgrade in air_upgrades:
                    if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                        core.research(upgrade)
                        break

        for archive in self.structures(UnitTypeId.TEMPLARARCHIVE).ready.idle:
            upgrade = UpgradeId.PSISTORMTECH
            if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                archive.research(upgrade)

        for bay in self.structures(UnitTypeId.ROBOTICSBAY).ready.idle:
            upgrade = UpgradeId.EXTENDEDTHERMALLANCE
            if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                bay.research(upgrade)

        for shrine in self.structures(UnitTypeId.DARKSHRINE).ready.idle:
            upgrade = UpgradeId.DARKTEMPLARBLINKUPGRADE
            if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                shrine.research(upgrade)

        fleet_upgrades = [UpgradeId.VOIDRAYSPEEDUPGRADE, UpgradeId.TEMPESTGROUNDATTACKUPGRADE]
        for beacon in self.structures(UnitTypeId.FLEETBEACON).ready.idle:
            for upgrade in fleet_upgrades:
                if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                    beacon.research(upgrade)
                    break

    # ------------------------------------------------------------- scout/def

    async def manage_scout(self):
        if self.active_build == "proxy_gates":
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
        if scout.shield_percentage < 0.1 and scout.health < 18:
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

        visible_bases = self.enemy_structures.of_type(ENEMY_TOWNHALLS)
        if visible_bases.amount > self._enemy_base_count:
            self._enemy_base_count = visible_bases.amount

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
        if self.cannon_targets and t < 360:
            for target in self.cannon_targets:
                already_on_it = self.workers.filter(lambda w: w.order_target == target.tag).amount
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

    # ------------------------------------------------------------------ army

    def _attack_target(self):
        army = self.units.of_type(ARMY_TYPES)
        reference = army.center if army else self.start_location
        if self.enemy_structures:
            return self.enemy_structures.closest_to(reference).position
        visible = self.enemy_units.filter(lambda u: u.type_id not in IGNORE_TARGETS)
        if visible:
            return visible.closest_to(reference).position
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

    @staticmethod
    def _unit_power(u):
        dps = max(u.ground_dps, u.air_dps)
        if dps <= 0:
            dps = CASTER_NOMINAL_DPS.get(u.type_id, 0)
        if dps <= 0:
            return 0.0
        return dps * (u.health + u.shield)

    def _our_power(self, units):
        return sum(self._unit_power(u) for u in units)

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
        self._cached_enemies = self.enemy_units.filter(lambda u: u.type_id not in IGNORE_TARGETS)
        self._focus_board = {}
        self._cached_army_for_spread = army if self._splash_threat else None
        self._collect_dodge_zones()
        self._blink_escaped = set()

        # Blink micro: low-shield stalkers jump out of the fight.
        if self.already_pending_upgrade(UpgradeId.BLINKTECH) == 1 and self._cached_enemies:
            low = [
                s for s in army
                if s.type_id == UnitTypeId.STALKER
                and s.shield_percentage < 0.15
                and self._cached_enemies.closer_than(9, s)
            ][:4]
            if low:
                try:
                    abilities_list = await self.get_available_abilities(low)
                    for stalker, abilities in zip(low, abilities_list):
                        if AbilityId.EFFECT_BLINK_STALKER in abilities:
                            threat = self._cached_enemies.closest_to(stalker)
                            stalker(
                                AbilityId.EFFECT_BLINK_STALKER,
                                stalker.position.towards(threat.position, -7),
                            )
                            self._blink_escaped.add(stalker.tag)
                except Exception:
                    pass

        build = self.active_build
        t = self.time
        est = self._army_supply_est()

        if build == "proxy_gates":
            if self.units(UnitTypeId.ZEALOT).amount * 2 >= cfg["attack_min"] or t > 170:
                self.attack_mode = True
        else:
            wg_done = self.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) == 1
            gate_ok = wg_done or t > 350
            if not self.attack_mode and t > self._retreat_until and gate_ok and est >= cfg["attack_min"] * (1 + 0.15 * min(3, self._retreat_count)):
                self.attack_mode = True
            if self.attack_mode and est <= cfg["retreat_at"]:
                self.attack_mode = False
                self._retreat_until = t + 10
                self._retreat_count += 1
            if self.supply_used > 190:
                self.attack_mode = True

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
        elif build == "proxy_gates":
            target = self.proxy_point
        elif self.enemy_rush_detected and t < 360 and self.townhalls.amount == 1:
            target = self.start_location.towards(self.game_info.map_center, 6)
        else:
            target = self.staging_point if t > 200 else self.natural_position

        center = army.center
        for unit in army:
            if unit.tag in self._blink_escaped:
                continue
            if (
                self.attack_mode
                and not defending
                and unit.distance_to(center) > 28
                and not self._cached_enemies.closer_than(11, unit)
            ):
                unit.move(center)
                continue
            if self._micro_special(unit, target, center):
                continue
            if unit.type_id in MELEE_TYPES:
                self._micro_melee(unit, target)
            else:
                self._micro_ranged(unit, target, prefer_armored=(unit.type_id == UnitTypeId.IMMORTAL))

        for observer in self.units(UnitTypeId.OBSERVER):
            if observer.distance_to(center) > 7:
                observer.move(center)

        # Proxy all-in probe pull.
        if (
            build == "proxy_gates"
            and self.attack_mode
            and not self.probes_pulled
            and self.units(UnitTypeId.ZEALOT).amount >= 3
            and t > 160
        ):
            self.probes_pulled = True
            pulled = 0
            for worker in self.workers:
                if pulled >= 6:
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

    def _spread_if_needed(self, unit):
        if self._splash_threat and self._cached_army_for_spread:
            crowd = self._cached_army_for_spread.filter(
                lambda a: a.tag != unit.tag and a.distance_to(unit) < 1.3
            )
            if crowd:
                self._flee(unit, crowd.closest_to(unit).position, 1.5)
                return True
        return False

    def _micro_melee(self, unit, target_point):
        if self._dodge(unit):
            return
        enemies = self._cached_enemies
        if unit.weapon_cooldown == 0 and enemies:
            best = self._best_target(unit, enemies, by_distance=True)
            if best is not None:
                unit.attack(best)
                return
        if unit.weapon_cooldown == 0:
            structures_in_range = self.enemy_structures.filter(lambda s: unit.target_in_range(s))
            if structures_in_range:
                def struct_priority(s):
                    return (0 if s.type_id in DEFENSIVE_STRUCTS else 1, s.health + s.shield)
                unit.attack(min(structures_in_range, key=struct_priority))
                return
        if self._spread_if_needed(unit):
            return
        self._ordered_attack_point(unit, target_point)

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
        """Cast Protoss combat abilities before standard weapon control."""
        enemies = self._cached_enemies

        if unit.type_id == UnitTypeId.SENTRY:
            local = enemies.closer_than(9, unit)
            if (
                local.amount >= 3
                and unit.energy >= 75
                and not unit.has_buff(BuffId.GUARDIANSHIELD)
                and self._can_cast_again(unit, AbilityId.GUARDIANSHIELD_GUARDIANSHIELD, 12.0)
            ):
                unit(AbilityId.GUARDIANSHIELD_GUARDIANSHIELD)
                return True
            choke_target = self._cluster_anchor(local.not_flying, 2.0, 5)
            if (
                choke_target is not None
                and unit.energy >= 50
                and self._can_cast_again(unit, AbilityId.FORCEFIELD_FORCEFIELD, 5.0)
            ):
                unit(AbilityId.FORCEFIELD_FORCEFIELD, choke_target.position)
                return True
            return False

        if unit.type_id == UnitTypeId.HIGHTEMPLAR:
            if self._dodge(unit):
                return True
            local = enemies.closer_than(10, unit)
            storm_target = self._cluster_anchor(local, 2.5, 4)
            if (
                self.already_pending_upgrade(UpgradeId.PSISTORMTECH) == 1
                and unit.energy >= 75
                and storm_target is not None
                and self._can_cast_again(unit, AbilityId.PSISTORM_PSISTORM, 3.5)
            ):
                unit(AbilityId.PSISTORM_PSISTORM, storm_target.position)
                return True
            energized = local.filter(lambda e: e.energy >= 50)
            if (
                unit.energy >= 50
                and energized
                and self._can_cast_again(unit, AbilityId.FEEDBACK_FEEDBACK, 2.0)
            ):
                unit(AbilityId.FEEDBACK_FEEDBACK, max(energized, key=lambda e: e.energy))
                return True
            if unit.distance_to(army_center) > 6:
                unit.move(army_center.towards(self.start_location, 2))
            return True

        if unit.type_id == UnitTypeId.DISRUPTOR:
            ground = enemies.not_flying.closer_than(13, unit)
            nova_target = self._cluster_anchor(ground, 2.5, 4)
            if (
                nova_target is not None
                and self._can_cast_again(unit, AbilityId.EFFECT_PURIFICATIONNOVA, 15.0)
            ):
                unit(AbilityId.EFFECT_PURIFICATIONNOVA, nova_target.position)
                return True
            if unit.distance_to(army_center) > 7:
                unit.move(army_center.towards(self.start_location, 3))
            return True

        if unit.type_id == UnitTypeId.VOIDRAY:
            armored = enemies.closer_than(7, unit).filter(lambda e: e.is_armored)
            if (
                armored
                and not unit.has_buff(BuffId.VOIDRAYSWARMDAMAGEBOOST)
                and self._can_cast_again(unit, AbilityId.EFFECT_VOIDRAYPRISMATICALIGNMENT, 18.0)
            ):
                unit(AbilityId.EFFECT_VOIDRAYPRISMATICALIGNMENT)
                return True
            return False

        if unit.type_id == UnitTypeId.PHOENIX:
            liftable = enemies.not_flying.closer_than(7, unit).filter(
                lambda e: not e.is_massive and (e.can_attack_air or e.type_id in SPECIAL_TARGETS)
            )
            if (
                unit.energy >= 50
                and liftable
                and self._can_cast_again(unit, AbilityId.GRAVITONBEAM_GRAVITONBEAM, 8.0)
            ):
                unit(AbilityId.GRAVITONBEAM_GRAVITONBEAM, max(liftable, key=lambda e: e.health + e.shield))
                return True
            return False

        if unit.type_id == UnitTypeId.ORACLE:
            ground = enemies.not_flying.closer_than(5, unit)
            beam_on = unit.has_buff(BuffId.ORACLEWEAPON)
            if ground and unit.energy >= 25 and not beam_on and self._can_cast_again(
                unit, AbilityId.BEHAVIOR_PULSARBEAMON, 3.0
            ):
                unit(AbilityId.BEHAVIOR_PULSARBEAMON)
                return True
            if (not ground or unit.energy < 5) and beam_on and self._can_cast_again(
                unit, AbilityId.BEHAVIOR_PULSARBEAMOFF, 3.0
            ):
                unit(AbilityId.BEHAVIOR_PULSARBEAMOFF)
                return True
            return False

        if unit.type_id == UnitTypeId.MOTHERSHIP:
            local = enemies.closer_than(12, unit)
            warp_target = self._cluster_anchor(local, 3.0, 5)
            if (
                unit.energy >= 100
                and warp_target is not None
                and self._can_cast_again(unit, AbilityId.EFFECT_TIMEWARP, 15.0)
            ):
                unit(AbilityId.EFFECT_TIMEWARP, warp_target.position)
                return True
            return False

        if unit.type_id == UnitTypeId.WARPPRISM:
            if unit.distance_to(army_center) > 7:
                unit.move(army_center)
            return True
        return False

    def _micro_ranged(self, unit, target_point, prefer_armored=False):
        if self._dodge(unit):
            return
        enemies = self._cached_enemies
        if self._preserve_hurt(unit, enemies):
            return
        if unit.weapon_cooldown == 0:
            if enemies:
                best = self._best_target(unit, enemies, prefer_armored=prefer_armored)
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
                    self._flee(unit, threat.position, 2.0)
                    return
                if unit.distance_to(threat) > my_range:
                    unit.move(threat.position)
                return
        if self._spread_if_needed(unit):
            return
        self._ordered_attack_point(unit, target_point)
