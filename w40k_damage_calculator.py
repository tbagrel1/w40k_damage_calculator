import sys
import json
import math
import click
from copy import deepcopy

def compute_wound_chance(strength, toughness, transhuman):
    if strength >= 2 * toughness:
        return 3 if transhuman else 5 
    elif strength > toughness:
        return 3 if transhuman else 4
    elif strength == toughness:
        return 3
    elif 2 * strength > toughness:
        return 2
    else:
        return 1

def compute_ifs(ap, sv, isv, aoc):
    if isv == 0 or isv is None:
        isv = 7
    fs = min(
        isv,
        sv + max(0, -ap - aoc)
    )
    ifs = min(6, max(0, fs - 1))
    return ifs

def compute_dmg(damage_prof, target_wounds, red):
    if not isinstance(damage_prof, int):
        if "+" in damage_prof:
            damage_prof_2, raw_fix_dam = damage_prof.split("+")
            fix_dam = int(raw_fix_dam)
        else:
            damage_prof_2 = damage_prof
            fix_dam = 0
        raw_n, raw_d = damage_prof_2.split("D")
        if raw_n:
            n = int(raw_n)
        else:
            n = 1
        dices = [[]]
        for _ in range(n):
            if raw_d == "3":
                dices = sum([[dcs + [1], dcs + [2], dcs + [3]] for dcs in dices], [])
            elif raw_d == "6":
                dices = sum([[dcs + [1], dcs + [2], dcs + [3], dcs + [4], dcs + [5], dcs + [6]] for dcs in dices], [])
            else:
                raise Exception(f"Cannot handle damage profile {damage_prof}!")
        dices_eff = [min(target_wounds, apply_red(sum(dcs) + fix_dam, red)) for dcs in dices]
        eff_d = sum(dices_eff) / len(dices_eff)
        return eff_d
    else:
        return min(apply_red(damage_prof, red), target_wounds)

def apply_red(base_dmg, red):
    if not red:
        return base_dmg
    if red[0] == "-":
        return max(1, base_dmg - int(red[1:]))
    elif red[1] == "/":
        return max(1, math.ceil(base_dmg / int(red[1:])))

def show_profile(aoc_enabled, T, W, Sv, ISv, hasAoC, **other):
    return f"[T{T}/{W}W/{Sv}+" + (f"/{ISv}++" if (ISv != 0 and ISv is not None) else "") + (", AoC" if aoc_enabled and hasAoC else "") + "]"

def fightRM(unit, target, aoc_enabled, defense=False):
    target_name = target["name"] if not defense else unit["name"]
    weight = target["weight"] if not defense else unit["weight"]
    W = target["W"]
    action_desc = "Attacking" if not defense else "Attacked by"
    print(f"- {action_desc} {target_name} " + (show_profile(aoc_enabled, **target) + " " if not defense else "") + f"(weight {weight*100:.0f}%)")
    tR, effR = fight("R", unit, target, aoc_enabled)
    tM, effM = fight("M", unit, target, aoc_enabled)
    total = tR + tM
    if "psykerMW" in unit:
        # counted twice for melee + range
        psyker_mw = unit["psykerMW"]
        total -= psyker_mw
    efficiency = ((total / (W * target["unit_size"])) * target["points"]) / unit["points"]
    print(" -> Total: " + f"{total:.2f}W (eff: {efficiency:.2f}, {total / W:.2f} dead models)")
    return [total, efficiency, tR, effR, tM, effM]

def make_dmg_repr(D, W, red):
    if isinstance(D, int):
        return str(min(apply_red(D, red), W))
    else:
        return f"min({D}{red}, W)"

def fight(mode, unit, target, aoc_enabled):
    total = 0
    W = target["W"]
    for weapon in unit["weapons" + mode]:
        weapon_name = weapon["name"]
        mwonw6 = weapon["MWonW6"] if "MWonW6" in weapon else 0
        hits = weapon["hits"]
        D = weapon["D"]
        iwsbs = 6 - weapon["WS/BS"] + 1
        wc = compute_wound_chance(weapon["S"], target["T"], target["transhuman"] if "transhuman" in target else False)
        ifs = compute_ifs(weapon["AP"], target["Sv"], target["ISv"], target["hasAoC"] and aoc_enabled)
        dmg_red = target["dmgRed"] if "dmgRed" in target else ""
        if "DonVehicle" in weapon and "vehicle" in target and target["vehicle"]:
            DonVehicle = weapon["DonVehicle"]
            dmg_repr = make_dmg_repr(DonVehicle, W, dmg_red)
            dmg = compute_dmg(DonVehicle, W, dmg_red)
        else:
            dmg_repr = make_dmg_repr(D, W, dmg_red)
            dmg = compute_dmg(D, W, dmg_red)
        if "rerollHitFull" in weapon and weapon["rerollHitFull"]:
            rrhc = "!!"
            hit_prob = iwsbs/6 + (1-iwsbs/6)*(iwsbs/6)
        elif "rerollHit1" in weapon and weapon["rerollHit1"]:
            rrhc = "!"
            hit_prob = iwsbs/6 + (1/6)*(iwsbs/6)
        else:
            rrhc = ""
            hit_prob = iwsbs/6
        if "rerollWoundFull" in weapon and weapon["rerollWoundFull"]:
            rrwc = "!!"
            wound_prob = wc/6 + (1-wc/6)*(wc/6)
        elif "rerollWound1" in weapon and weapon["rerollWound1"]:
            rrwc = "!"
            wound_prob = wc/6 + (1/6)*(wc/6)
        else:
            rrwc = ""
            wound_prob = wc/6
        total_weapon = hits * hit_prob * wound_prob * (ifs/6) * dmg + hits * hit_prob * (1/6) * mwonw6
        print(f"  + {weapon_name}: {hits} x ({iwsbs}/6){rrhc} x ({wc}/6){rrwc} x ({ifs}/6) x {dmg_repr}" + (f" + {hits} x ({iwsbs}/6) x (1/6) x {mwonw6} MW " if mwonw6 > 0 else "") + f" = {total_weapon:.2f} ({total_weapon / W:.2f} dead models)")
        total += total_weapon
    if "psykerMW" in unit:
        psyker_mw = unit["psykerMW"]
        print(f"  + Psyker Smite: ~{psyker_mw} MW")
        total += psyker_mw
    efficiency = ((total / (W * target["unit_size"])) * target["points"]) / unit["points"]
    print(f"  -> " + ("Ranged: " if mode == "R" else "Melee: ") + f"{total:.2f}W (eff: {efficiency:.2f}, {total / W:.2f} dead models)")
    return total, efficiency

def round(unit, targets, aoc):
    print("# Attack")
    atk_scores = [[target["weight"]] + fightRM(unit, target, aoc) for target in targets]
    atk_wounds = sum(t[0] * t[1] for t in atk_scores)
    atk_eff = sum(t[0] * t[2] for t in atk_scores)
    atk_wounds_r = sum(t[0] * t[3] for t in atk_scores)
    atk_eff_r = sum(t[0] * t[4] for t in atk_scores)
    atk_wounds_m = sum(t[0] * t[5] for t in atk_scores)
    atk_eff_m = sum(t[0] * t[6] for t in atk_scores)
    print(f">> R AVG: {atk_wounds_r:.2f}W inflicted (dmg score {atk_eff_r:.2f})\n   M AVG: {atk_wounds_m:.2f}W inflicted (dmg score {atk_eff_m:.2f})\n   R+M AVG: {atk_wounds:.2f}W inflicted (dmg score: {atk_eff:.2f})\n")
    print("# Defense")
    def_scores = [[target["weight"]] + fightRM(target, unit, aoc, True) for target in targets]
    def_wounds = sum(t[0] * t[1] for t in def_scores)
    def_eff = sum(t[0] * t[2] for t in def_scores)
    def_wounds_r = sum(t[0] * t[3] for t in def_scores)
    def_eff_r = sum(t[0] * t[4] for t in def_scores)
    def_wounds_m = sum(t[0] * t[5] for t in def_scores)
    def_eff_m = sum(t[0] * t[6] for t in def_scores)
    print(f">> R AVG: {def_wounds_r:.2f}W taken (tank score {1/def_eff_r:.2f})\n   M AVG: {def_wounds_m:.2f}W taken (tank score {1/def_eff_m:.2f})\n   R+M AVG: {def_wounds:.2f}W taken (tank score: {1/def_eff:.2f})\n")
    print("# Overall (dmg score x tank score)")
    print(f">> R/R eff: {atk_eff_r/def_eff_r:.2f}\n   M/M eff: {atk_eff_m/def_eff_m:.2f}\n   R+M/R+M eff: {atk_eff/def_eff:.2f}\n\n")

@click.command()
@click.option("--aoc/--no-aoc", default=True, help="Should it take into account the new *Armor of Contempt* rule?")
@click.option("--swap", is_flag=True, help="Should it analyse player 2 units (instead of player 1 units)?")
@click.argument("DATASHEETS", type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True))
@click.argument("BATTLE_PLAN", type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True))
def main(aoc, swap, datasheets, battle_plan):
    print(f"Reading datasheets from {datasheets}...")
    with open(datasheets, "r", encoding="utf-8") as datasheet_file:
        all_units = json.load(datasheet_file)
    all_units_dict = { unit["name"]: unit for unit in all_units }
    print(f"Reading battle plan from {battle_plan}...")
    with open(battle_plan, "r", encoding="utf-8") as battle_file:
        data = json.load(battle_file)
    targets = []
    for d in data["player_2"]:
        t = deepcopy(all_units_dict[d["name"]])
        t["unit_nb"] = d["unit_nb"]
        targets.append(t)
    units = []
    for d in data["player_1"]:
        t = deepcopy(all_units_dict[d["name"]])
        t["unit_nb"] = d["unit_nb"]
        units.append(t)
    if swap:
        targets, units = units, targets
    total_targets_wounds = sum(target["unit_size"] * target["unit_nb"] * target["W"] for target in targets)
    for target in targets:
        target["weight"] = (target["unit_size"] * target["unit_nb"] * target["W"]) / total_targets_wounds
    for unit in units:
        unit_name = unit["name"]
        aoc_string = "(with Armor of Contempt)" if aoc else "(without Armor of Contempt)"
        print(f"### {unit_name} {aoc_string} ###\n")
        round(unit, targets, aoc)

if __name__ == "__main__":
    main()

