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

ARMY_TYPES = {UnitTypeId.ZEALOT, UnitTypeId.STALKER, UnitTypeId.IMMORTAL}
WORKER_TYPES = {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE, UnitTypeId.MULE}
IGNORE_TARGETS = {UnitTypeId.LARVA, UnitTypeId.EGG, UnitTypeId.BROODLING, UnitTypeId.INTERCEPTOR}
SCOUT_IGNORE = {UnitTypeId.OVERLORD, UnitTypeId.OVERSEER, UnitTypeId.OBSERVER}
ENEMY_TOWNHALLS = {
    UnitTypeId.HATCHERY, UnitTypeId.LAIR, UnitTypeId.HIVE, UnitTypeId.NEXUS,
    UnitTypeId.COMMANDCENTER, UnitTypeId.ORBITALCOMMAND, UnitTypeId.PLANETARYFORTRESS,
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
        self._first_pressure_recorded = False
        self._point_order_cache = {}
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
            self.strategy = StrategyManager(getattr(self, "opponent_id", None), race_name)
        except Exception:
            self.strategy = None

        try:
            if self.strategy:
                if self.strategy.expects("rushed") or self.strategy.expects("worker_rush"):
                    self.enemy_rush_detected = True
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
    def active_build(self):
        build = self.strategy.build if self.strategy else "four_gate"
        if build == "proxy_gates" and self.time > 420:
            return "four_gate"
        return build

    @property
    def all_in(self):
        return self.active_build == "proxy_gates" or self.supply_used > 190 or not self.townhalls

    def _army_supply_est(self):
        return (
            2 * self.units(UnitTypeId.ZEALOT).amount
            + 2 * self.units(UnitTypeId.STALKER).amount
            + 4 * self.units(UnitTypeId.IMMORTAL).amount
        )

    def _base_build_config(self):
        t = self.time
        build = self.active_build
        bases = max(1, self.townhalls.amount)
        if build == "proxy_gates":
            return dict(
                probe_cap=17, gas_target=0, gate_cap=0,
                want_robo=False, want_twilight=False, want_forge=False,
                immortal_cap=0, observer_cap=0,
                attack_min=6, retreat_at=0,
            )
        if build == "four_gate":
            transitioned = t > 390
            return dict(
                probe_cap=(23 if not transitioned else min(66, 22 * bases)),
                gas_target=(1 if t < 145 else 2),
                gate_cap=(1 if t < 100 else 4) if not transitioned else 5,
                want_robo=transitioned, want_twilight=transitioned, want_forge=t > 420,
                immortal_cap=(2 if transitioned else 0), observer_cap=(1 if transitioned else 0),
                attack_min=16, retreat_at=6,
            )
        # stalker_immortal
        if bases < 2:
            gate_cap = 1
        elif bases == 2:
            gate_cap = 3
        else:
            gate_cap = 6
        return dict(
            probe_cap=min(66, 22 * bases),
            gas_target=(1 if t < 150 else min(2 * bases, 6)),
            gate_cap=gate_cap,
            want_robo=(t > 210 and bases >= 2),
            want_twilight=t > 300,
            want_forge=t > 320,
            immortal_cap=4, observer_cap=(2 if self.cloak_threat else 1),
            attack_min=42, retreat_at=18,
        )

    def build_config(self):
        cfg = self._base_build_config()
        try:
            if self.strategy:
                cfg["attack_min"] = max(2, int(round(cfg["attack_min"] * self.strategy.aggression_mult)))
        except Exception:
            pass
        if (self.cloak_threat or self._max_air_threat >= 3) and self.active_build != "proxy_gates":
            cfg["want_forge"] = cfg["want_forge"] or self.time > 160
            cfg["observer_cap"] = max(cfg["observer_cap"], 1)
        return cfg

    # ------------------------------------------------------------------ frame

    async def on_step(self, iteration: int):
        try:
            await self._step(iteration)
        except Exception:
            pass

    async def _step(self, iteration: int):
        if not self.greeted and iteration >= 2:
            self.greeted = True
            try:
                await self.chat_send("(glhf) Protodd online.")
                if self.strategy:
                    await self.chat_send("Tag:" + self.strategy.build)
                    await self.chat_send("Tag:aggr_" + self.strategy.aggression)
            except Exception:
                pass

        if iteration % 64 == 0 and iteration > 0:
            try:
                avg_ms = self.step_time[1]
                if avg_ms > 66 and self.client.game_step < 4:
                    self.client.game_step = 4
                elif avg_ms < 25 and self.client.game_step > 2:
                    self.client.game_step = 2
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
        await self._safe(self.control_army(cfg))
        if iteration % 16 == 0:
            await self._safe(self.distribute_workers())

    async def _safe(self, coro):
        try:
            await coro
        except Exception:
            pass

    async def on_end(self, game_result):
        try:
            if self.strategy:
                self.strategy.report(game_result == Result.Victory)
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
            if enemies.filter(lambda u: u.is_cloaked or u.type_id in CLOAK_UNIT_HINTS):
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
        t = self.time
        build = self.active_build
        bases = self.townhalls.amount
        if build == "proxy_gates" or self._base_threats:
            return False
        if build == "four_gate":
            if bases == 1:
                return t > 390
            return self.minerals > 500 and t > 520
        if bases == 1:
            return t > 155 and not (self.enemy_rush_detected and t < 240)
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
            if robos == 0 and self.can_afford(UnitTypeId.ROBOTICSFACILITY):
                await self._build_near(UnitTypeId.ROBOTICSFACILITY, anchor, step=3)

        if cfg["want_twilight"] and core_ready:
            tc = self.structures(UnitTypeId.TWILIGHTCOUNCIL).amount + self.already_pending(UnitTypeId.TWILIGHTCOUNCIL)
            if tc == 0 and self.can_afford(UnitTypeId.TWILIGHTCOUNCIL):
                await self._build_near(UnitTypeId.TWILIGHTCOUNCIL, anchor, step=3)

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
            if nat_nexus:
                if not self.structures(UnitTypeId.PYLON).ready.closer_than(7, self.natural_position):
                    if self.can_afford(UnitTypeId.PYLON):
                        await self._build_near(UnitTypeId.PYLON, self.natural_position.towards(self.game_info.map_center, 4), step=2)
                else:
                    batteries = self.structures(UnitTypeId.SHIELDBATTERY).amount + self.already_pending(UnitTypeId.SHIELDBATTERY)
                    if batteries < 1 and self.can_afford(UnitTypeId.SHIELDBATTERY):
                        await self._build_near(UnitTypeId.SHIELDBATTERY, self.natural_position.towards(self.game_info.map_center, 3), step=2)

    async def manage_proxy(self, cfg):
        build = self.strategy.build if self.strategy else ""
        t = self.time
        # four_gate proxy pylon for forward warp-ins.
        if self.active_build == "four_gate" and 200 < t < 400:
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

        if build != "proxy_gates" or t > 420:
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

        # Morph gateways once warpgate tech is done.
        if wg_done:
            for gate in self.structures(UnitTypeId.GATEWAY).ready.idle:
                gate(AbilityId.MORPH_WARPGATE)

        stalkers = self.units(UnitTypeId.STALKER).amount
        zealots = self.units(UnitTypeId.ZEALOT).amount
        core_ready = bool(self.structures(UnitTypeId.CYBERNETICSCORE).ready)

        def want_stalker():
            if not core_ready:
                return False
            if self.vespene < 50:
                return False
            return stalkers <= 3 * max(1, zealots) or self.active_build != "proxy_gates"

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
                    if AbilityId.WARPGATETRAIN_STALKER not in abilities:
                        continue
                    if want_stalker() and self.can_afford(UnitTypeId.STALKER):
                        unit_type = UnitTypeId.STALKER
                        warp_ability = AbilityId.WARPGATETRAIN_STALKER
                    elif self.can_afford(UnitTypeId.ZEALOT):
                        unit_type = UnitTypeId.ZEALOT
                        warp_ability = AbilityId.WARPGATETRAIN_ZEALOT
                    else:
                        break
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
            if want_stalker() and self.can_afford(UnitTypeId.STALKER):
                gate.train(UnitTypeId.STALKER)
            elif self.can_afford(UnitTypeId.ZEALOT):
                gate.train(UnitTypeId.ZEALOT)

        # Robo production: observers first, then immortals.
        observers = self.units(UnitTypeId.OBSERVER).amount + self.already_pending(UnitTypeId.OBSERVER)
        immortals = self.units(UnitTypeId.IMMORTAL).amount + self.already_pending(UnitTypeId.IMMORTAL)
        for robo in self.structures(UnitTypeId.ROBOTICSFACILITY).ready.idle:
            if self.supply_left < 1:
                break
            if observers < cfg["observer_cap"] and self.can_afford(UnitTypeId.OBSERVER):
                robo.train(UnitTypeId.OBSERVER)
                observers += 1
            elif (
                immortals < cfg["immortal_cap"]
                and self.supply_left >= 4
                and self.can_afford(UnitTypeId.IMMORTAL)
            ):
                robo.train(UnitTypeId.IMMORTAL)
                immortals += 1

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

        for tc in self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready.idle:
            if self.already_pending_upgrade(UpgradeId.BLINKTECH) == 0 and self.can_afford(UpgradeId.BLINKTECH):
                tc.research(UpgradeId.BLINKTECH)
            elif (
                self.already_pending_upgrade(UpgradeId.BLINKTECH) == 1
                and self.already_pending_upgrade(UpgradeId.CHARGE) == 0
                and self.can_afford(UpgradeId.CHARGE)
            ):
                tc.research(UpgradeId.CHARGE)

        twilight_ready = bool(self.structures(UnitTypeId.TWILIGHTCOUNCIL).ready)
        ground_upgrades = [
            (UpgradeId.PROTOSSGROUNDWEAPONSLEVEL1, False),
            (UpgradeId.PROTOSSGROUNDARMORSLEVEL1, False),
            (UpgradeId.PROTOSSGROUNDWEAPONSLEVEL2, True),
            (UpgradeId.PROTOSSGROUNDARMORSLEVEL2, True),
        ]
        for forge in self.structures(UnitTypeId.FORGE).ready.idle:
            for upgrade, needs_tc in ground_upgrades:
                if needs_tc and not twilight_ready:
                    continue
                if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                    forge.research(upgrade)
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
                scout.move(self.enemy_natural.towards(self.game_info.map_center, 4))
                scout.move(self.enemy_start_locations[0].towards(self.enemy_natural, 6), queue=True)
            else:
                self.scout_tag = None
                if self.townhalls:
                    mfs = self.mineral_field.closer_than(10, self.townhalls.first)
                    if mfs:
                        scout.gather(mfs.random)

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
        threats = self.enemy_units.filter(
            lambda u: u.type_id not in SCOUT_IGNORE
            and u.type_id not in IGNORE_TARGETS
            and any(u.distance_to(th) < 25 for th in ths)
        )
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

    async def control_army(self, cfg):
        army = self.units.of_type(ARMY_TYPES)
        if not army:
            return
        self._cached_enemies = self.enemy_units.filter(lambda u: u.type_id not in IGNORE_TARGETS)
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
            if not self.attack_mode and t > self._retreat_until and est >= cfg["attack_min"] and gate_ok:
                self.attack_mode = True
            if self.attack_mode and est <= cfg["retreat_at"]:
                self.attack_mode = False
                self._retreat_until = t + 10
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

        defending = self._base_threats is not None
        if defending:
            target = self._base_threats.closest_to(self.start_location).position
        elif self.attack_mode:
            target = self._attack_target()
        elif build == "proxy_gates":
            target = self.proxy_point
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
            if unit.type_id == UnitTypeId.ZEALOT:
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

    def _ordered_attack_point(self, unit, point):
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

    def _best_target(self, unit, enemies, by_distance=False, prefer_armored=False):
        in_range = enemies.filter(lambda e: unit.target_in_range(e))
        if not in_range:
            return None

        def priority(e):
            if e.type_id in SPECIAL_TARGETS:
                group = 0
            elif e.can_attack_ground or e.can_attack_air:
                group = 1
            elif e.type_id in WORKER_TYPES:
                group = 2
            else:
                group = 3
            if prefer_armored and e.is_armored:
                group -= 0.5
            metric = e.distance_to(unit) if by_distance else (e.health + e.shield)
            return (group, metric)

        return min(in_range, key=priority)

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

    def _micro_ranged(self, unit, target_point, prefer_armored=False):
        if self._dodge(unit):
            return
        enemies = self._cached_enemies
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
