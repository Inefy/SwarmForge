"""
Zacling - adaptive Zerg bot for the aiarena.net ladder.
Same architecture as Battencruiser: per-opponent build/timing bandits,
opponent fingerprinting, fight-or-flee power evaluation, effect dodging,
range-aware stutter micro, splash spreading.

Builds:
  * roach_timing - pool-first expand into a roach/ling timing, then macro
  * macro_hydra  - 3-base roach/hydra with double evo upgrades
  * twelve_pool  - 12-pool speedling rush (transitions out if it doesn't kill)
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

MELEE_TYPES = {UnitTypeId.ZERGLING, UnitTypeId.BANELING, UnitTypeId.ULTRALISK}
ARMY_TYPES = MELEE_TYPES | {
    UnitTypeId.ROACH, UnitTypeId.RAVAGER, UnitTypeId.HYDRALISK,
    UnitTypeId.LURKERMP, UnitTypeId.LURKERMPBURROWED, UnitTypeId.INFESTOR,
    UnitTypeId.SWARMHOSTMP, UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR,
    UnitTypeId.VIPER, UnitTypeId.BROODLORD,
}
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


class ZaclingBot(BotAI):
    NAME = "Zacling"
    RACE_NAME = "Zerg"

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
        self._stats = {}
        self._retreat_count = 0
        self._cached_enemies = None
        self._cached_army_for_spread = None
        self._dodge_zones = []
        self.natural_position = None
        self.enemy_natural = None
        self.staging_point = None
        self.overlord_park = None
        self._ol_scout_tag = None

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
        self.overlord_park = self.start_location.towards(self.game_info.map_center, -6)

    @property
    def _rushing(self):
        return (
            bool(self.strategy)
            and getattr(self.strategy, "pool_timing", "pool16") == "pool12"
            and self.time < 300
        )

    @property
    def active_build(self):
        # Synthetic label: openings are now composed from learned parameters.
        return "twelve_pool" if self._rushing else "macro"

    @property
    def all_in(self):
        return self._rushing or self.supply_used > 190 or not self.townhalls

    def _army_supply_est(self):
        return float(self.supply_army)

    def _base_build_config(self):
        t = self.time
        s = self.strategy
        gas0 = {"no_gas": 0, "one_gas": 1, "two_gas": 2}.get(getattr(s, "gas_open", "one_gas"), 1)
        bases = max(1, self.townhalls.amount)
        if self._rushing:
            return dict(
                drone_cap=14, gas_target=1, queen_cap=1,
                want_warren=False, want_lair=False, want_den=False,
                evo_count=0, want_spines=False,
                attack_min=2, retreat_at=0, ling_only=True,
                want_bane=False, want_infest=False, want_spire=False,
                want_lurker=False, want_hive=False, want_ultra=False,
            )
        developed = bases >= 2 or t > 330
        if not developed:
            drone_cap = 30
            gas_target = gas0 if t < 160 else max(gas0, 1)
        else:
            drone_cap = min(70, 20 * bases + 4)
            gas_target = 2 if bases < 3 else 4
        evo_count = 0
        if t > 330:
            evo_count = 2 if bases >= 3 else 1
        return dict(
            drone_cap=drone_cap,
            gas_target=gas_target,
            queen_cap=min(5, bases + 1),
            want_warren=t > 130,
            want_lair=(developed and t > 250),
            want_den=t > 380,
            evo_count=evo_count,
            want_spines=self.enemy_rush_detected,
            attack_min=26, retreat_at=12, ling_only=False,
            want_bane=False, want_infest=False, want_spire=False,
            want_lurker=False, want_hive=False, want_ultra=False,
        )

    def build_config(self):
        cfg = self._base_build_config()
        try:
            if self.strategy:
                cfg["attack_min"] = max(2, int(round(cfg["attack_min"] * self.strategy.aggression_mult)))
                cfg["drone_cap"] = max(12, int(round(cfg["drone_cap"] * self.strategy.greed_worker_mult)))
        except Exception:
            pass
        # Known cloak/air opponents: lair + den + spores early.
        if self.cloak_threat or self._max_air_threat >= 3:
            if self.active_build != "twelve_pool":
                cfg["want_lair"] = cfg["want_lair"] or self.time > 200
                cfg["want_den"] = cfg["want_den"] or self.time > 240
        # Learned tech focus.
        try:
            tech = self.strategy.tech if self.strategy else "roach_focus"
            if self.active_build != "twelve_pool":
                if tech == "hydra_focus":
                    cfg["want_lair"] = cfg["want_lair"] or self.time > 220
                    cfg["want_den"] = cfg["want_den"] or self.time > 260
                    if self.time > 240:
                        cfg["gas_target"] = max(cfg["gas_target"], 3)
                elif tech == "ling_flood":
                    cfg["gas_target"] = min(cfg["gas_target"], 1)
                army = getattr(self.strategy, "army", "ranged")
                if army == "swarm":
                    cfg["want_bane"] = self.time > 160
                    cfg["want_infest"] = self.time > 440
                    cfg["gas_target"] = max(cfg["gas_target"], 3)
                elif army == "ranged":
                    cfg["want_lair"] = self.time > 200
                    cfg["want_den"] = self.time > 250
                    cfg["want_lurker"] = self.time > 460
                    cfg["gas_target"] = max(cfg["gas_target"], 4)
                elif army == "sky":
                    cfg["want_lair"] = self.time > 190
                    cfg["want_spire"] = self.time > 330
                    cfg["want_infest"] = self.time > 520
                    cfg["want_hive"] = self.time > 620
                    cfg["gas_target"] = max(cfg["gas_target"], 6)
                else:
                    cfg["want_lair"] = self.time > 190
                    cfg["want_den"] = self.time > 260
                    cfg["want_infest"] = self.time > 380
                    cfg["want_lurker"] = self.time > 460
                    cfg["want_hive"] = self.time > 540
                    cfg["want_ultra"] = self.time > 650
                    cfg["want_spire"] = self.time > 440
                    cfg["gas_target"] = max(cfg["gas_target"], 6)
        except Exception:
            pass
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
                await self.chat_send("(glhf)")
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
        await self._safe(self.manage_larva(cfg))
        await self._safe(self.manage_morphs(cfg))
        await self._safe(self.manage_queens(cfg))
        await self._safe(self.manage_gas(cfg))
        await self._safe(self.manage_expansion(cfg))
        await self._safe(self.manage_tech(cfg))
        await self._safe(self.manage_upgrades(cfg))
        await self._safe(self.manage_overlords())
        await self._safe(self.manage_defense())
        await self._safe(self.control_army(cfg))
        if iteration % 16 == 0:
            self._track_stats()
            await self._safe(self.distribute_workers())

    async def _safe(self, coro):
        try:
            await coro
        except Exception:
            pass

    async def on_end(self, game_result):
        try:
            if self.strategy:
                try:
                    self._stats["retreats"] = self._retreat_count
                except Exception:
                    pass
                self.strategy.report(game_result == Result.Victory, self._stats)
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

    async def manage_larva(self, cfg):
        larvae = self.larva
        if not larvae:
            return
        # Overlords
        if self.supply_cap < 200:
            pending = self.already_pending(UnitTypeId.OVERLORD)
            threshold = 2 + min(8, self.townhalls.amount * 3)
            max_pending = 2 if self.supply_cap > 40 else 1
            if self.supply_left < threshold and pending < max_pending:
                if self.can_afford(UnitTypeId.OVERLORD):
                    larvae.random.train(UnitTypeId.OVERLORD)
                return
        # Drones (economy first while safe)
        drones = self.supply_workers + self.already_pending(UnitTypeId.DRONE)
        if drones < cfg["drone_cap"] and self.supply_left >= 1 and self.can_afford(UnitTypeId.DRONE):
            if not self._base_threats or drones < 12:
                larvae.random.train(UnitTypeId.DRONE)
                if larvae.amount <= 1:
                    return
        # Army from remaining larva. The learned army plan opens the whole tech tree.
        pool_ready = bool(self.structures(UnitTypeId.SPAWNINGPOOL).ready)
        warren_ready = bool(self.structures(UnitTypeId.ROACHWARREN).ready)
        den_ready = bool(self.structures(UnitTypeId.HYDRALISKDEN).ready)
        pit_ready = bool(self.structures(UnitTypeId.INFESTATIONPIT).ready)
        spire_ready = bool(self.structures.of_type({UnitTypeId.SPIRE, UnitTypeId.GREATERSPIRE}).ready)
        hive_ready = bool(self.townhalls(UnitTypeId.HIVE).ready)
        ultra_ready = bool(self.structures(UnitTypeId.ULTRALISKCAVERN).ready)
        army_plan = getattr(self.strategy, "army", "ranged") if self.strategy else "ranged"
        for larva in larvae:
            if self.supply_left < 1:
                break
            if cfg["ling_only"]:
                if pool_ready and self.can_afford(UnitTypeId.ZERGLING):
                    larva.train(UnitTypeId.ZERGLING)
                continue
            choices = []
            if army_plan == "swarm":
                if pool_ready:
                    choices.append(UnitTypeId.ZERGLING)
                if pit_ready:
                    choices += [UnitTypeId.INFESTOR, UnitTypeId.SWARMHOSTMP]
            elif army_plan == "ranged":
                if warren_ready:
                    choices.append(UnitTypeId.ROACH)
                if den_ready:
                    choices.append(UnitTypeId.HYDRALISK)
            elif army_plan == "sky":
                if spire_ready:
                    choices += [UnitTypeId.MUTALISK, UnitTypeId.CORRUPTOR]
                if hive_ready:
                    choices.append(UnitTypeId.VIPER)
            else:
                if ultra_ready:
                    choices.append(UnitTypeId.ULTRALISK)
                if pit_ready:
                    choices += [UnitTypeId.INFESTOR, UnitTypeId.SWARMHOSTMP]
                if hive_ready:
                    choices.append(UnitTypeId.VIPER)
                if den_ready:
                    choices.append(UnitTypeId.HYDRALISK)
                if spire_ready:
                    choices.append(UnitTypeId.CORRUPTOR)
            if pool_ready:
                choices.append(UnitTypeId.ZERGLING)
            choices.sort(key=lambda u: self.units(u).amount + self.already_pending(u))
            choice = next((u for u in choices if self.can_afford(u)), None)
            if choice is not None:
                larva.train(choice)

    async def manage_morphs(self, cfg):
        army_plan = getattr(self.strategy, "army", "ranged") if self.strategy else "ranged"
        if cfg["want_bane"] and self.structures(UnitTypeId.BANELINGNEST).ready:
            target = max(2, self.units(UnitTypeId.ZERGLING).amount // 4)
            existing = self.units(UnitTypeId.BANELING).amount + self.already_pending(UnitTypeId.BANELING)
            for ling in self.units(UnitTypeId.ZERGLING).idle:
                if existing >= target or not self.can_afford(UnitTypeId.BANELING):
                    break
                ling(AbilityId.MORPHZERGLINGTOBANELING_BANELING)
                existing += 1
        if army_plan in {"ranged", "hive"}:
            target = max(2, self.units(UnitTypeId.ROACH).amount // 4)
            existing = self.units(UnitTypeId.RAVAGER).amount + self.already_pending(UnitTypeId.RAVAGER)
            for roach in self.units(UnitTypeId.ROACH).idle:
                if existing >= target or not self.can_afford(UnitTypeId.RAVAGER):
                    break
                roach(AbilityId.MORPHTORAVAGER_RAVAGER)
                existing += 1
        if cfg["want_lurker"] and self.structures(UnitTypeId.LURKERDENMP).ready:
            target = max(2, self.units(UnitTypeId.HYDRALISK).amount // 3)
            existing = self.units(UnitTypeId.LURKERMP).amount + self.already_pending(UnitTypeId.LURKERMP)
            for hydra in self.units(UnitTypeId.HYDRALISK).idle:
                if existing >= target or not self.can_afford(UnitTypeId.LURKERMP):
                    break
                hydra(AbilityId.MORPH_LURKER)
                existing += 1
        if self.structures(UnitTypeId.GREATERSPIRE).ready and army_plan in {"sky", "hive"}:
            target = max(2, self.units(UnitTypeId.CORRUPTOR).amount // 2)
            existing = self.units(UnitTypeId.BROODLORD).amount + self.already_pending(UnitTypeId.BROODLORD)
            for corruptor in self.units(UnitTypeId.CORRUPTOR).idle:
                if existing >= target or not self.can_afford(UnitTypeId.BROODLORD):
                    break
                corruptor(AbilityId.MORPHTOBROODLORD_BROODLORD)
                existing += 1

    async def manage_queens(self, cfg):
        pool_ready = bool(self.structures(UnitTypeId.SPAWNINGPOOL).ready)
        queens = self.units(UnitTypeId.QUEEN)
        if pool_ready and queens.amount + self.already_pending(UnitTypeId.QUEEN) < cfg["queen_cap"]:
            for th in self.townhalls.ready.idle:
                if self.can_afford(UnitTypeId.QUEEN) and self.supply_left >= 2:
                    th.train(UnitTypeId.QUEEN)
                    break
        # Inject
        for queen in queens.idle:
            if queen.energy >= 25:
                targets = self.townhalls.ready.filter(
                    lambda th: not th.has_buff(BuffId.QUEENSPAWNLARVATIMER)
                )
                if targets:
                    queen(AbilityId.EFFECT_INJECTLARVA, targets.closest_to(queen))
        # Queens help defend their bases.
        if self._base_threats:
            for queen in queens:
                threat = self._base_threats.closest_to(queen)
                if threat.distance_to(queen) < 14 and queen.weapon_cooldown == 0:
                    queen.attack(threat)

    async def manage_gas(self, cfg):
        if not (self.structures(UnitTypeId.SPAWNINGPOOL) or self.already_pending(UnitTypeId.SPAWNINGPOOL)):
            return
        current = self.gas_buildings.amount + self.already_pending(UnitTypeId.EXTRACTOR)
        if current >= cfg["gas_target"] or not self.can_afford(UnitTypeId.EXTRACTOR):
            return
        for th in self.townhalls.ready:
            for vg in self.vespene_geyser.closer_than(10, th):
                if self.gas_buildings.filter(lambda u: u.distance_to(vg) < 1):
                    continue
                worker = self.select_build_worker(vg.position)
                if worker is not None:
                    worker.build_gas(vg)
                    return

    def _wants_expand(self):
        t = self.time + (self.strategy.greed_expand_shift if self.strategy else 0)
        bases = self.townhalls.amount
        if self._rushing or self._base_threats:
            return False
        arm = getattr(self.strategy, "pool_timing", "pool16") if self.strategy else "pool16"
        pool_started = bool(self.structures(UnitTypeId.SPAWNINGPOOL)) or self.already_pending(UnitTypeId.SPAWNINGPOOL)
        if bases == 1:
            if arm == "hatch_first":
                return t > 55
            if arm == "pool12":
                return t > 270
            return t > 95 and pool_started
        if bases == 2:
            return t > 280
        if bases == 3:
            return t > 430
        return self.minerals > 500 and t > 540

    async def manage_expansion(self, cfg):
        if not self._wants_expand():
            return
        if self.already_pending(UnitTypeId.HATCHERY):
            return
        if not self.can_afford(UnitTypeId.HATCHERY):
            return
        location = await self.get_next_expansion()
        if location is None:
            return
        if self.enemy_units.filter(lambda u: u.distance_to(location) < 12).amount >= 2:
            return
        worker = self.select_build_worker(location)
        if worker is not None and self.can_afford(UnitTypeId.HATCHERY):
            worker.build(UnitTypeId.HATCHERY, location)

    async def _build_near(self, type_id, near, step=4):
        location = await self.find_placement(type_id, near, placement_step=step)
        if location is None:
            return False
        worker = self.select_build_worker(location)
        if worker is None:
            return False
        worker.build(type_id, location)
        return True

    async def manage_tech(self, cfg):
        t = self.time
        hq = self.townhalls.first
        near = hq.position.towards(self.game_info.map_center, 6)

        # Spawning pool: immediately for twelve_pool, ~16 supply otherwise.
        pool_count = self.structures(UnitTypeId.SPAWNINGPOOL).amount + self.already_pending(UnitTypeId.SPAWNINGPOOL)
        if pool_count == 0 and self.can_afford(UnitTypeId.SPAWNINGPOOL):
            arm = getattr(self.strategy, "pool_timing", "pool16") if self.strategy else "pool16"
            if (
                arm == "pool12"
                or (arm == "pool16" and (self.supply_workers >= 15 or t > 90))
                or (
                    arm == "hatch_first"
                    and (
                        self.townhalls.amount >= 2
                        or self.already_pending(UnitTypeId.HATCHERY)
                        or t > 120
                    )
                )
            ):
                await self._build_near(UnitTypeId.SPAWNINGPOOL, near)

        pool_ready = bool(self.structures(UnitTypeId.SPAWNINGPOOL).ready)

        if cfg["want_warren"] and pool_ready:
            count = self.structures(UnitTypeId.ROACHWARREN).amount + self.already_pending(UnitTypeId.ROACHWARREN)
            if count == 0 and self.can_afford(UnitTypeId.ROACHWARREN):
                await self._build_near(UnitTypeId.ROACHWARREN, near)

        # Lair
        if cfg["want_lair"] and pool_ready:
            has_lair = self.townhalls.of_type({UnitTypeId.LAIR, UnitTypeId.HIVE}).amount + self.already_pending(UnitTypeId.LAIR)
            if has_lair == 0 and self.can_afford(UnitTypeId.LAIR):
                home = self.townhalls(UnitTypeId.HATCHERY).ready.idle
                if home:
                    home.closest_to(self.start_location).build(UnitTypeId.LAIR)

        # Hydralisk den
        if cfg["want_den"] and self.townhalls.of_type({UnitTypeId.LAIR, UnitTypeId.HIVE}).ready:
            count = self.structures(UnitTypeId.HYDRALISKDEN).amount + self.already_pending(UnitTypeId.HYDRALISKDEN)
            if count == 0 and self.can_afford(UnitTypeId.HYDRALISKDEN):
                await self._build_near(UnitTypeId.HYDRALISKDEN, near)

        if cfg["want_bane"] and pool_ready:
            count = self.structures(UnitTypeId.BANELINGNEST).amount + self.already_pending(UnitTypeId.BANELINGNEST)
            if count == 0 and self.can_afford(UnitTypeId.BANELINGNEST):
                await self._build_near(UnitTypeId.BANELINGNEST, near)

        lair_ready = bool(self.townhalls.of_type({UnitTypeId.LAIR, UnitTypeId.HIVE}).ready)
        if cfg["want_infest"] and lair_ready:
            count = self.structures(UnitTypeId.INFESTATIONPIT).amount + self.already_pending(UnitTypeId.INFESTATIONPIT)
            if count == 0 and self.can_afford(UnitTypeId.INFESTATIONPIT):
                await self._build_near(UnitTypeId.INFESTATIONPIT, near)

        if cfg["want_spire"] and lair_ready:
            count = self.structures.of_type({UnitTypeId.SPIRE, UnitTypeId.GREATERSPIRE}).amount + self.already_pending(UnitTypeId.SPIRE)
            if count == 0 and self.can_afford(UnitTypeId.SPIRE):
                await self._build_near(UnitTypeId.SPIRE, near)

        if cfg["want_lurker"] and self.structures(UnitTypeId.HYDRALISKDEN).ready and lair_ready:
            count = self.structures(UnitTypeId.LURKERDENMP).amount + self.already_pending(UnitTypeId.LURKERDENMP)
            if count == 0 and self.can_afford(UnitTypeId.LURKERDENMP):
                await self._build_near(UnitTypeId.LURKERDENMP, near)

        if cfg["want_hive"] and self.structures(UnitTypeId.INFESTATIONPIT).ready:
            if not self.townhalls(UnitTypeId.HIVE) and self.already_pending(UnitTypeId.HIVE) == 0:
                lairs = self.townhalls(UnitTypeId.LAIR).ready.idle
                if lairs and self.can_afford(UnitTypeId.HIVE):
                    lairs.first(AbilityId.UPGRADETOHIVE_HIVE)

        if self.townhalls(UnitTypeId.HIVE).ready and cfg["want_spire"]:
            if not self.structures(UnitTypeId.GREATERSPIRE) and self.already_pending(UnitTypeId.GREATERSPIRE) == 0:
                spires = self.structures(UnitTypeId.SPIRE).ready.idle
                if spires and self.can_afford(UnitTypeId.GREATERSPIRE):
                    spires.first(AbilityId.UPGRADETOGREATERSPIRE_GREATERSPIRE)

        if cfg["want_ultra"] and self.townhalls(UnitTypeId.HIVE).ready:
            count = self.structures(UnitTypeId.ULTRALISKCAVERN).amount + self.already_pending(UnitTypeId.ULTRALISKCAVERN)
            if count == 0 and self.can_afford(UnitTypeId.ULTRALISKCAVERN):
                await self._build_near(UnitTypeId.ULTRALISKCAVERN, near)

        # Evolution chambers
        if cfg["evo_count"] > 0 and pool_ready:
            count = self.structures(UnitTypeId.EVOLUTIONCHAMBER).amount + self.already_pending(UnitTypeId.EVOLUTIONCHAMBER)
            if count < cfg["evo_count"] and self.can_afford(UnitTypeId.EVOLUTIONCHAMBER):
                await self._build_near(UnitTypeId.EVOLUTIONCHAMBER, near)

        # Spines at the natural against rushes.
        if cfg["want_spines"] and pool_ready and t < 420:
            nat_hatch = self.townhalls.filter(lambda x: x.distance_to(self.natural_position) < 8)
            anchor = self.natural_position if nat_hatch else hq.position
            spines = self.structures(UnitTypeId.SPINECRAWLER).amount + self.already_pending(UnitTypeId.SPINECRAWLER)
            if spines < 2 and self.can_afford(UnitTypeId.SPINECRAWLER):
                await self._build_near(
                    UnitTypeId.SPINECRAWLER, Point2(anchor).towards(self.game_info.map_center, 4), step=2
                )

        # Spores at every mineral line against cloak/air.
        if (self.cloak_threat or self._max_air_threat >= 3) and pool_ready:
            if self.can_afford(UnitTypeId.SPORECRAWLER):
                needed = 2 if self._max_air_threat >= 5 else 1
                for th in self.townhalls.ready:
                    if self.structures(UnitTypeId.SPORECRAWLER).closer_than(9, th).amount >= needed:
                        continue
                    mfs = self.mineral_field.closer_than(10, th)
                    anchor = mfs.center.towards(th.position, 2) if mfs else th.position
                    if await self._build_near(UnitTypeId.SPORECRAWLER, anchor, step=2):
                        break

        # Overseer against cloak.
        if self.cloak_threat and self.townhalls.of_type({UnitTypeId.LAIR, UnitTypeId.HIVE}).ready:
            overseers = self.units(UnitTypeId.OVERSEER).amount + self.already_pending(UnitTypeId.OVERSEER)
            if overseers < 1 and self.can_afford(UnitTypeId.OVERSEER):
                overlords = self.units(UnitTypeId.OVERLORD)
                if overlords:
                    overlords.random(AbilityId.MORPH_OVERSEER)

    async def manage_upgrades(self, cfg):
        if self.active_build == "twelve_pool" and self.time < 300:
            # Only ling speed matters.
            for pool in self.structures(UnitTypeId.SPAWNINGPOOL).ready.idle:
                if (
                    self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 0
                    and self.can_afford(UpgradeId.ZERGLINGMOVEMENTSPEED)
                ):
                    pool.research(UpgradeId.ZERGLINGMOVEMENTSPEED)
            return

        for pool in self.structures(UnitTypeId.SPAWNINGPOOL).ready.idle:
            if (
                self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 0
                and self.can_afford(UpgradeId.ZERGLINGMOVEMENTSPEED)
            ):
                pool.research(UpgradeId.ZERGLINGMOVEMENTSPEED)

        lair_up = bool(self.townhalls.of_type({UnitTypeId.LAIR, UnitTypeId.HIVE}).ready)
        for warren in self.structures(UnitTypeId.ROACHWARREN).ready.idle:
            if (
                lair_up
                and self.already_pending_upgrade(UpgradeId.GLIALRECONSTITUTION) == 0
                and self.can_afford(UpgradeId.GLIALRECONSTITUTION)
            ):
                warren.research(UpgradeId.GLIALRECONSTITUTION)

        for den in self.structures(UnitTypeId.HYDRALISKDEN).ready.idle:
            if (
                self.already_pending_upgrade(UpgradeId.EVOLVEGROOVEDSPINES) == 0
                and self.can_afford(UpgradeId.EVOLVEGROOVEDSPINES)
            ):
                den.research(UpgradeId.EVOLVEGROOVEDSPINES)
            elif (
                self.already_pending_upgrade(UpgradeId.EVOLVEMUSCULARAUGMENTS) == 0
                and self.can_afford(UpgradeId.EVOLVEMUSCULARAUGMENTS)
            ):
                den.research(UpgradeId.EVOLVEMUSCULARAUGMENTS)

        ground_upgrades = [
            (UpgradeId.ZERGMISSILEWEAPONSLEVEL1, False),
            (UpgradeId.ZERGGROUNDARMORSLEVEL1, False),
            (UpgradeId.ZERGMELEEWEAPONSLEVEL1, False),
            (UpgradeId.ZERGMISSILEWEAPONSLEVEL2, True),
            (UpgradeId.ZERGGROUNDARMORSLEVEL2, True),
            (UpgradeId.ZERGMELEEWEAPONSLEVEL2, True),
        ]
        for evo in self.structures(UnitTypeId.EVOLUTIONCHAMBER).ready.idle:
            for upgrade, needs_lair in ground_upgrades:
                if needs_lair and not lair_up:
                    continue
                if self.already_pending_upgrade(upgrade) == 0 and self.can_afford(upgrade):
                    evo.research(upgrade)
                    break

    async def manage_overlords(self):
        overlords = self.units(UnitTypeId.OVERLORD)
        if not overlords:
            return
        # First overlord scouts toward the enemy natural, pulls back at 2:30.
        if self._ol_scout_tag is None and self.time < 30:
            scout = overlords.first
            self._ol_scout_tag = scout.tag
            scout.move(self.enemy_natural.towards(self.game_info.map_center, 8))
        scout = overlords.find_by_tag(self._ol_scout_tag) if self._ol_scout_tag else None
        if scout is not None:
            if self.time > 150 or scout.health_percentage < 0.7:
                scout.move(self.overlord_park)
                self._ol_scout_tag = 0  # stop managing it
        # Everyone else parks behind the main.
        for ol in overlords.idle:
            if scout is not None and ol.tag == scout.tag:
                continue
            if ol.distance_to(self.overlord_park) > 12:
                try:
                    ol.move(self.overlord_park.random_on_distance(5))
                except Exception:
                    ol.move(self.overlord_park)

    # ---------------------------------------------------------------- defense

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
        for unit in self.workers | self.units.of_type(ARMY_TYPES) | self.units(UnitTypeId.QUEEN):
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

        build = self.active_build
        t = self.time
        est = self._army_supply_est()

        if build == "twelve_pool":
            if self.units(UnitTypeId.ZERGLING).amount >= cfg["attack_min"] or t > 150:
                self.attack_mode = True
        else:
            if not self.attack_mode and t > self._retreat_until and est >= cfg["attack_min"] * (1 + 0.15 * min(3, self._retreat_count)):
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
            target = self._base_threats.closest_to(self.start_location).position
        elif self.attack_mode:
            target = self._attack_target()
        else:
            target = self.staging_point if t > 200 else self.natural_position

        center = army.center
        for unit in army:
            if (
                self.attack_mode
                and not defending
                and unit.distance_to(center) > 28
                and not self._cached_enemies.closer_than(11, unit)
            ):
                unit.move(center)
                continue
            if unit.type_id == UnitTypeId.LURKERMP:
                if self._cached_enemies.closer_than(10, unit):
                    unit(AbilityId.BURROWDOWN_LURKER)
                else:
                    unit.move(target)
            elif unit.type_id == UnitTypeId.LURKERMPBURROWED:
                if not self._cached_enemies.closer_than(12, unit) and unit.distance_to(target) > 14:
                    unit(AbilityId.BURROWUP_LURKER)
            elif unit.type_id in MELEE_TYPES:
                self._micro_melee(unit, target)
            else:
                self._micro_ranged(unit, target)

        # Overseer shadows the army for detection.
        for overseer in self.units(UnitTypeId.OVERSEER):
            if overseer.distance_to(center) > 8:
                overseer.move(center)

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

    def _best_target(self, unit, enemies, by_distance=False):
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
            metric = e.distance_to(unit) if by_distance else (e.health + e.shield)
            return (group, metric)

        return min(in_range, key=priority)

    def _spread_if_needed(self, unit):
        if self._splash_threat and self._cached_army_for_spread:
            crowd = self._cached_army_for_spread.filter(
                lambda a: a.tag != unit.tag and a.distance_to(unit) < 1.2
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
        structures_in_range = self.enemy_structures.filter(lambda s: unit.target_in_range(s))
        if structures_in_range and unit.weapon_cooldown == 0:
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

    def _micro_ranged(self, unit, target_point):
        if self._dodge(unit):
            return
        enemies = self._cached_enemies
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
