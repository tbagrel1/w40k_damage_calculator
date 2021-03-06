import json
import math
import click
import os
import re
from copy import deepcopy

# TODO: optimal profile calculator: choose M, R, or M+R on a per-unit basis based on efficiency
# TODO: Melee efficiency means nothing for vehicle (but be careful blast)
# TODO: indirect damage capping
# TODO: handle D6 on attackNb +blast
# TODO: immobile bonus on CC + penalty on melee
# TODO: bypass save (1 MW and attack sequence end) on W6 (eg. Celestine)
# TODO: repentia +1 wound

VERBOSE = True

ALLOWED_KEYWORDS = [
    "transhuman",
    "vehicle",
    "armorOfContempt",
    "titanic",
    "infantry",
    "biker"
]

def vprint(*args, **kwargs):
    if VERBOSE:
        print(*args, **kwargs)

def compute_wound_chance(strength, toughness, transhuman, override):
    if override is not None:
        return 6 - override + 1
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

def parse_sv(string):
    if string == "null":
        return 7
    return int(string.rstrip("+"))

def compute_ifs(mode, ap, raw_sv, raw_isv, aoc):
    if isinstance(raw_sv, int) or isinstance(raw_sv, float) or raw_sv is None:
        sv = raw_sv if raw_sv is not None else 7
    else:
        ranged_raw_sv, melee_raw_sv = raw_sv.split("<>")
        sv = parse_sv(melee_raw_sv) if mode == "melee" else parse_sv(ranged_raw_sv)
    if isinstance(raw_isv, int) or isinstance(raw_isv, float) or raw_isv is None:
        isv = raw_isv if raw_isv is not None else 7
    else:
        ranged_raw_isv, melee_raw_isv = raw_isv.split("<>")
        isv = parse_sv(melee_raw_isv) if mode == "melee" else parse_sv(ranged_raw_isv)
    fs = max(2, min(
        isv,
        sv + max(0, -ap - aoc)
    ))
    ifs = max(0, fs - 1)
    return ifs

def compute_dmg(damage_prof, target_wounds, red, ifnp):
    if not isinstance(damage_prof, int) and "+" in damage_prof and "<>" in damage_prof:
        base, fix_range = damage_prof.split("+")
        fix_1, fix_2 = fix_range.split("<>")
        wp1, wp2 = f"{base}+{fix_1}", f"{base}+{fix_2}"
        df1 = compute_dmg_(wp1, target_wounds, red, ifnp)
        df2 = compute_dmg_(wp2, target_wounds, red, ifnp)
        avg = (df1 + df2) / 2
        # print(f"DEBUG: with {wp1}: {df1}W, with {wp2}: {df2}W, avg: {avg}")
        return avg
    else:
        return compute_dmg_(damage_prof, target_wounds, red, ifnp)

def compute_dmg_(damage_prof, target_wounds, red, ifnp):
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
        dices_eff = [min(target_wounds, apply_red(sum(dcs) + fix_dam, red) * (ifnp / 6)) for dcs in dices]
        eff_d = sum(dices_eff) / len(dices_eff)
        return eff_d
    else:
        return min(apply_red(damage_prof, red), target_wounds)

def apply_red(base_dmg, red):
    if not red:
        return base_dmg
    if red[0] == "-":
        return max(1, base_dmg - int(red[1:]))
    elif red[0] == "/":
        return max(1, math.ceil(base_dmg / int(red[1:])))

def show_profile(aoc_enabled, T, W, Sv, ISv, keywords, cost, **other):
    return f"[T{T}/{W}W/{Sv}+" + (f"/{ISv}++" if (ISv != 0 and ISv is not None) else "") + (", AoC" if aoc_enabled and "armorOfContempt" in keywords else "") + f", {cost}pts]"

def fightRM(unit, target, aoc_enabled, defense=False):
    target_id = target["id"] if not defense else unit["id"]
    weight = target["weight"] if not defense else unit["weight"]
    W = target["W"]
    action_desc = "Attacking" if not defense else "Attacked by"
    vprint(f"- {action_desc} {target_id} " + (show_profile(aoc_enabled, **target) + " " if not defense else "") + f"(weight {weight*100:.0f}%)")
    tR, effR, w100R = fight("ranged", unit, target, aoc_enabled, defense)
    tM, effM, w100M = fight("melee", unit, target, aoc_enabled, defense)
    mode_kw = "def" if defense else "atk"
    total = tR + tM
    if "psykerMW" in unit:
        # counted twice for melee + range
        psyker_mw = unit["psykerMW"]
        total -= psyker_mw
    efficiency = ((total / (W * target["unitSize"])) * target["cost"]) / unit["cost"]
    w100 = w100R + w100M
    w100_result = "" if defense else f"{w100:.2f} W/100pts, "
    vprint(" -> Total: " + f"{total:.2f}W ({w100_result}{mode_kw} ratio: {efficiency:.2f}, {total / W:.2f} dead models)")
    return [total, efficiency, w100, tR, effR, w100R, tM, effM, w100M]

def make_dmg_repr(D, W, red, ifnp):
    if isinstance(D, int) and ifnp == 6:
        return str(min(apply_red(D, red), W))
    else:
        fnp_repr = f" x ({ifnp}/6)" if ifnp != 6 else ""
        return f"min({D}{red}{fnp_repr}, {W})"

def fight(mode, unit, target, aoc_enabled, defense=False):
    mode_kw = "def" if defense else "atk"
    total = 0
    W = target["W"]
    for weapon in unit[mode + "Weapons"]:
        weapon_name = weapon["name"]
        mwonw6 = weapon["MW@W6"] if "MW@W6" in weapon else 0
        hits = weapon["attackNb"]
        D = weapon["D"]
        wsbs = weapon["WS/BS"]
        if wsbs is not None:
        # Grim resolve:
            if "Dark Angels" in unit["groups"] and mode == "ranged":
                wsbs = max(2, wsbs - 0.5)
            iwsbs = 6 - wsbs + 1
        else:
            iwsbs = 6
        wc = compute_wound_chance(
            weapon["S"], target["T"], "transhuman" in target["keywords"],
            weapon["W@InfantryBiker"] if ("W@InfantryBiker" in weapon and "transhuman" not in target["keywords"] and ("infantry" in target["keywords"] or "biker" in target["keywords"])) else None
        )
        ifs = compute_ifs(mode, weapon["AP"], target["Sv"], target["ISv"], "armorOfContempt" in target["keywords"]  and aoc_enabled)
        dmg_red = target["damageReduction"] if "damageReduction" in target else ""
        if "feelNoPain" in target:
            ifnp = target["feelNoPain"] - 1
        else:
            ifnp = 6
        if "feelNoPain@MW" in target:
            ifnp_mw = target["feelNoPain@MW"] - 1
        elif "feelNoPain" in target:
            ifnp_mw = target["feelNoPain"] - 1
        else:
            ifnp_mw = 6
        if "D@Vehicle" in weapon and "vehicle" in target["keywords"]:
            DonVehicle = weapon["D@Vehicle"]
            dmg_repr = make_dmg_repr(DonVehicle, W, dmg_red, ifnp)
            dmg = compute_dmg(DonVehicle, W, dmg_red, ifnp)
        else:
            dmg_repr = make_dmg_repr(D, W, dmg_red, ifnp)
            dmg = compute_dmg(D, W, dmg_red, ifnp)
        if "reroll@HF" in weapon and weapon["reroll@HF"]:
            rrhc = "!!"
            hit_prob = iwsbs/6 + (1-iwsbs/6)*(iwsbs/6)
        elif "reroll@H1" in weapon and weapon["reroll@H1"]:
            rrhc = "!"
            hit_prob = iwsbs/6 + (1/6)*(iwsbs/6)
        else:
            rrhc = ""
            hit_prob = iwsbs/6
        if "reroll@WF" in weapon and weapon["reroll@WF"]:
            rrwc = "!!"
            wound_prob = wc/6 + (1-wc/6)*(wc/6)
        elif "reroll@W1" in weapon and weapon["reroll@W1"]:
            rrwc = "!"
            wound_prob = wc/6 + (1/6)*(wc/6)
        else:
            rrwc = ""
            wound_prob = wc/6
        total_weapon = hits * hit_prob * wound_prob * (ifs/6) * dmg + hits * hit_prob * (1/6) * mwonw6 * (ifnp_mw / 6)
        fnp_mw_repr = f" x ({ifnp_mw}/6)" if ifnp_mw != 6 else ""
        vprint(f"  + {weapon_name}: {hits} x ({iwsbs}/6){rrhc} x ({wc}/6){rrwc} x ({ifs:.4g}/6) x {dmg_repr}" + (f" + {hits} x ({iwsbs}/6) x (1/6) x {mwonw6}{fnp_mw_repr} MW " if mwonw6 > 0 else "") + f" = {total_weapon:.2f} ({total_weapon / W:.2f} dead models)")
        total += total_weapon
    if "psykerMW" in unit:
        psyker_mw = unit["psykerMW"]
        vprint(f"  + Psyker Smite: ~{psyker_mw} MW")
        total += psyker_mw
    w100 = total / (unit["cost"] / 100)
    efficiency = ((total / (W * target["unitSize"])) * target["cost"]) / unit["cost"]
    w100_result = "" if defense else f"{w100:.2f} W/100pts, "
    vprint(f"  -> " + ("Ranged: " if mode == "ranged" else "Melee: ") + f"{total:.2f}W ({w100_result}{mode_kw} ratio: {efficiency:.2f}, {total / W:.2f} dead models)")
    return total, efficiency, w100

def round(unit, targets, aoc):
    vprint("# Attack")
    atk_scores = [[target["weight"]] + fightRM(unit, target, aoc) for target in targets]
    atk_wounds = sum(t[0] * t[1] for t in atk_scores)
    atk_eff = sum(t[0] * t[2] for t in atk_scores)
    atk_w100 = sum(t[0] * t[3] for t in atk_scores)
    atk_wounds_r = sum(t[0] * t[4] for t in atk_scores)
    atk_eff_r = sum(t[0] * t[5] for t in atk_scores)
    atk_w100_r = sum(t[0] * t[6] for t in atk_scores)
    atk_wounds_m = sum(t[0] * t[7] for t in atk_scores)
    atk_eff_m = sum(t[0] * t[8] for t in atk_scores)
    atk_w100_m = sum(t[0] * t[9] for t in atk_scores)
    print(f">> ATK R: {atk_wounds_r:.2f}W ({atk_w100_r:.2f} W/100pts, atk ratio {atk_eff_r:.2f})\n   ATK M: {atk_wounds_m:.2f}W ({atk_w100_m:.2f} W/100pts, atk ratio {atk_eff_m:.2f})\n   ATK R+M: {atk_wounds:.2f}W ({atk_w100:.2f} W/100pts, atk ratio: {atk_eff:.2f})")
    vprint()
    vprint("# Defense")
    def_scores = [[target["weight"]] + fightRM(target, unit, aoc, True) for target in targets]
    def_wounds = sum(t[0] * t[1] for t in def_scores)
    def_eff = sum(t[0] * t[2] for t in def_scores)
    def_wounds_r = sum(t[0] * t[4] for t in def_scores)
    def_eff_r = sum(t[0] * t[5] for t in def_scores)
    def_wounds_m = sum(t[0] * t[7] for t in def_scores)
    def_eff_m = sum(t[0] * t[8] for t in def_scores)
    vprint(f">> DEF R: {def_wounds_r:.2f}W taken (def ratio {def_eff_r:.2f})\n   DEF M: {def_wounds_m:.2f}W taken (def ratio {def_eff_m:.2f})\n   DEF R+M: {def_wounds:.2f}W taken (def ratio: {def_eff:.2f})\n")
    vprint("# Overall (score = atk ratio / def ratio)")
    print(f">> ATK/DEF R score: {atk_eff_r/def_eff_r:.2f}\n   ATK/DEF M score: {atk_eff_m/def_eff_m:.2f}\n   ATK/DEF R+M score: {atk_eff/def_eff:.2f}\n")
    vprint()

def load_datasheets(root_path):
    all_units_dict = {}
    groups_dict = {}
    add_datasheets(all_units_dict, groups_dict, [], root_path)
    return all_units_dict, groups_dict

def add_datasheets(all_units_dict, groups_dict, groups, path):
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            if root != path:
                continue
            for file in files:
                if not file.endswith(".json"):
                    continue
                group_name = file.split(".json")[0]
                add_datasheets(all_units_dict, groups_dict, groups + [group_name], os.path.join(root, file))
            for dir_ in dirs:
                add_datasheets(all_units_dict, groups_dict, groups + [dir_], os.path.join(root, dir_))
    else:
        print(f"Loading datasheets from {path}...")
        if not groups:
            raise Exception(f"Datasheets file {path} is not within an army directory.")
        army = groups[0]
        with open(path, "r", encoding="utf-8") as datasheets_file:
            content = json.load(datasheets_file)
        for datasheet in content:
            name = datasheet["name"]
            for keyword in datasheet["keywords"]:
                if keyword not in ALLOWED_KEYWORDS:
                    raise Exception(f"Keyword '{keyword}' is not allowed in datasheet '{name}' in {path}")
            datasheet_id = make_datasheet_id(army, datasheet)
            datasheet["id"] = datasheet_id
            datasheet["groups"] = groups
            if datasheet_id in all_units_dict:
                raise Exception(f"Datasheet with id '{datasheet_id}' is already present in the database (found the 2nd time in {path})")
            all_units_dict[datasheet_id] = datasheet
            for i in range(1, len(groups) + 1):
                group_id = "/".join(groups[:i])
                if group_id not in groups_dict:
                    groups_dict[group_id] = []
                groups_dict[group_id].append(datasheet)

def make_datasheet_id(army, datasheet):
    # "Dark Angels/Sicaran Omega@1/HB +2HB"
    name = datasheet["name"]
    unitSize = datasheet["unitSize"]
    variant = datasheet["variant"]
    return f"{army}/{name}@{unitSize}/{variant}"

@click.command()
@click.option("--aoc/--no-aoc", default=True, help="Should it take into account the new *Armor of Contempt* rule?")
@click.option("--swap", is_flag=True, help="Should it analyse player 2 units (instead of player 1 units)?")
@click.option("--verbose/--silent", default=True, help="Should it display detailed information on each battle?")
@click.argument("DATASHEETS_DIR", type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True))
@click.argument("BATTLE_PLAN", type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True))
def main(aoc, swap, verbose, datasheets_dir, battle_plan):
    global VERBOSE
    VERBOSE = verbose
    all_units_dict, groups_dict = load_datasheets(datasheets_dir)
    print(f"\nLoading battle plan from {battle_plan}...\n")
    with open(battle_plan, "r", encoding="utf-8") as battle_file:
        data = json.load(battle_file)
    units = []
    for d in data["player_1"]:
        if "include_group" in d:
            g = deepcopy(groups_dict[d["include_group"]])
            for t in g:
                t["unitNb"] = 1
            units.extend(g)
        elif "include_regex" in d:
            rr = "^" + d["include_regex"].replace("+", "\+") + "$"
            r = re.compile(rr)
            filtered = [deepcopy(all_units_dict[uid]) for uid in all_units_dict if r.search(uid) is not None]
            units.extend(filtered)
        else:
            t = deepcopy(all_units_dict[d["id"]])
            t["unitNb"] = d["unitNb"]
            units.append(t)
    targets = []
    for d in data["player_2"]:
        if "include_group" in d:
            g = deepcopy(groups_dict[d["include_group"]])
            for t in g:
                t["unitNb"] = 1
            targets.extend(g)
        elif "include_regex" in d:
            rr = "^" + d["include_regex"].replace("+", "\+") + "$"
            r = re.compile(rr)
            filtered = [deepcopy(all_units_dict[uid]) for uid in all_units_dict if r.search(uid) is not None]
            targets.extend(filtered)
        else:
            t = deepcopy(all_units_dict[d["id"]])
            t["unitNb"] = d["unitNb"]
            targets.append(t)
    if swap:
        targets, units = units, targets
    total_targets_wounds = sum(target["unitSize"] * target["unitNb"] * target["W"] for target in targets)
    for target in targets:
        target["weight"] = (target["unitSize"] * target["unitNb"] * target["W"]) / total_targets_wounds
    for unit in units:
        unit_id = unit["id"]
        print(f"## {unit_id} {show_profile(aoc, **unit)}")
        vprint()
        round(unit, targets, aoc)

if __name__ == "__main__":
    main()

