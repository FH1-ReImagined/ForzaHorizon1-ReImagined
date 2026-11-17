#!/usr/bin/env python3
"""An FM1 car as a car of its own in Forza Horizon (2012) (#930): a mod with its files, its
database rows and its name.

The rows are a donor FH1 car's, copied the way Forza Imported DLC Maker's fh1_port.clone_db
copies them: every row that names the donor's id (the car's own tables, its upgrade lists and
their parts' physics) is copied under the new id, and so is its body (Data_CarBody and the body
upgrades, which name the body by CarBodyID); the donor's engine, drivetrain and stock wheel are
shared. Rows that would put the car into the game's events, among the AI drivers or into the
rewards are left out. From FM1 come:
  - its id: 55000 + FM1's (carlist.xml's Ordinal), #924's rule for a car new to FH1;
  - its MediaName: FH1's code for its make, FM1's name and the year (TOY_BorderMR2_95);
  - its make and year (model.dat), and its names: FM1's (carlist.xml's displayName) and
    "<make> <name>", each with " (FM1)" (#930);
  - its paints: the liveries' colours, metallic or not (livery0-6.livery);
  - its steering wheel's and lamps' places (CarPartPositions 0-4 and 9);
  - its model, textures and lamps (fm1_cars).
Until FM1's physics are converted it drives as the donor, and shows the donor's thumbnail.

The mod (a folder for the mods folder):
  media/cars/<MediaName>/              the donor's files renamed, with FM1's models and textures
  media/db/gamedb.slt                  the game's database (or an FM4 cars mod's) with the rows:
                                       a car added by a database patch counts as not installed
                                       (fm4_cars.write_full_db; measured again here, the game
                                       fell back to another car of the garage)
  media/stringtables/<lang>/data_car.str  the game's table (or the FM4 mod's) with the names added
  media/audio/cars/...                 the donor's sound tuning, renamed
  media/ui/textures/thumbnails/thumbnail_<id>.xds
Standard library only.
"""
import math
import os
import re
import sqlite3
import struct
import tempfile

import fh1_carbin as fc
import fm1_cars

GAMEDB = "media/db/gamedb.slt"
BANKS = "@banks/"  # files keys of sound banks: they go into a package (write_package), not the mod
PACKAGE_NAME = "464D3143415253000000000000000000000000004D"  # the banks' package folder ("FM1CARS")
PACKAGE_OFFER = 64801  # a .puboffer no FH1 offer uses (the FM4 cars import's is 64800)
BANKS_FOLDER = "FM1Banks_pri_60"
NEW_ID = 55000  # #924: a car new to FH1 gets 55000 + its id in its own game
# Rows of a car that are not the car itself: events it is driven in, its car challenge (the title
# update's), AI drivers, rewards, DLC offers (fh1_port.clone_db leaves the events, the driver
# level rewards and the offers out too).
SKIP_TABLES = {"EventParticipants", "EventRecommendedCars", "FreeRoamDrivers", "Rewards_DriverLevelv3",
               "Rewards_Wristband", "ArcadeCarList", "ContentOffers", "ContentOffersMapping",
               "CarChallenges", "CarChallengeTitles"}
CAR_COLUMNS = {"ordinal", "carid", "rewardcarid", "modelid"}
CAR_ID_TABLES = {"Data_Car", "CarExceptions", "CameraOverrides", "CarTrackOffsets"}
NAME_TYPOS = {"Wiedebody": "Widebody"}  # FM1's own spelling in carlist.xml
# A car's own sound files, by its MediaName: where it sits (CMT), the engine's banks and their
# levels (ET; the game finds it by the engine's Data_Engine.MediaName) and its effects (DSP).
AUDIO = (("audio/cars/CarModelTuning.zip", "media/audio/cars/carmodeltuning/", "_CMT.xml"),
         ("audio/cars/Engines/EngineTuning.zip", "media/audio/cars/engines/enginetuning/", "_ET.xml"),
         ("audio/cars/Engines/DSPTuning.zip", "media/audio/cars/engines/dsptuning/", "_DSPTuning.xml"))
MOD_TOML = 'name = "%s"\ndescription = "%s"\n'


class Fm1ImportError(ValueError):
    pass


# ============================================================================= FM1's car data

def fm1_info(fm1_dir, cab, car):
    """FM1's data on a car: {name, display, ordinal, make, year, colours [(r, g, b, metallic)]}.
    cars/carlist.xml (Mega.ca2): <car name= displayName= Ordinal=>; forzadb/cardb/model.dat:
    104-byte rows, u32 ordinal at 0, make at 4, year at 16; make.dat: 28-byte rows, u32 id,
    region and string id, then the name (16 bytes); the car's liveries:
    cars/<car>/liveries/standard/livery<n>.livery (<override red= green= blue=>, the shader
    settings' file name says metallic or nonmetallic)."""
    mega = fm1_cars.Cab(os.path.join(fm1_dir, "media", "cabs", "Mega.ca2"))
    carlist = mega.read("cars/carlist.xml").decode("latin-1")
    m = re.search(r'<car name="(%s)"([^>]*)>' % re.escape(car), carlist, re.I)
    if not m:
        raise Fm1ImportError("FM1's carlist.xml has no car %s" % car)
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
    ordinal = int(attrs["Ordinal"])
    model = mega.read("forzadb/cardb/model.dat")
    row = next((model[o:o + 104] for o in range(0, len(model) - 103, 104)
                if struct.unpack_from("<I", model, o)[0] == ordinal), None)
    if row is None:
        raise Fm1ImportError("FM1's model.dat has no car %d" % ordinal)
    make_id, year = struct.unpack_from("<I", row, 4)[0], struct.unpack_from("<I", row, 16)[0]
    makes = mega.read("forzadb/cardb/make.dat")
    make = next((makes[o + 12:o + 28].split(b"\0")[0].decode("latin-1") for o in range(0, len(makes) - 27, 28)
                 if struct.unpack_from("<I", makes, o)[0] == make_id), attrs.get("Make", ""))
    colours = []
    for k in range(16):
        key = "cars\\%s\\liveries\\standard\\livery%d.livery" % (car.lower(), k)
        if key not in cab.files:
            break
        text = cab.read(key).decode("latin-1")
        rgb = re.search(r"<override red=(\d+) green=(\d+) blue=(\d+)", text)
        if rgb:
            colours.append(tuple(int(v) for v in rgb.groups()) + (0 if "nonmetallic" in text.lower() else 1,))
    return {"name": m.group(1), "display": attrs.get("displayName", m.group(1)), "ordinal": ordinal,
            "make": make, "year": year, "colours": colours}


def names(info):
    """(DisplayName, ModelShort) as FH1 writes them ("Supra RZ", "Toyota Supra '98"), with " (FM1)".
    A trailing "(Widebody)" of FM1's becomes part of the name."""
    display = info["display"].strip()
    for bad, good in NAME_TYPOS.items():
        display = display.replace(bad, good)
    if " " not in display:  # an internal-style name ("HondaIntegraR"): split at its capitals and digits
        display = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Z][a-z])", " ", display)
    if info["make"] and display.lower().startswith(info["make"].lower() + " "):  # "Honda S2000"
        display = display[len(info["make"]) + 1:].strip()
    short = re.sub(r"\s*\([^)]*\)\s*$", "", display).strip()
    display = re.sub(r"\s*\(([^)]*)\)\s*$", r" \1", display).strip()
    return display + " (FM1)", "%s %s (FM1)" % (info["make"], short)


def media_name(code, info, taken=lambda m: False):
    """<make code>_<FM1's name>_<year>; with FM1 after the name when FH1 has that name already
    (FM1's s7 against FH1's SAL_S7_04)."""
    name = re.sub(r"[^A-Za-z0-9]", "", info["name"])
    name = name[:1].upper() + name[1:]
    out = "%s_%s_%02d" % (code, name, info["year"] % 100)
    if taken(out):
        out = "%s_%sFM1_%02d" % (code, name, info["year"] % 100)
    return out


# ============================================================================= the rows

def columns(db, table):
    return [r[1] for r in db.execute("pragma table_info([%s])" % table)]


def clone_rows(db, donor, new, skip=SKIP_TABLES):
    """{table: [row dicts]}: the donor's rows under the new id (the car, its body, the upgrade
    lists and their physics rows with the car's id x 1000 + n). Only rows that name the donor."""
    r = db.execute("select CarBodyID from List_UpgradeCarBody where Ordinal = ? and IsStock = 1", (donor,)).fetchone()
    body = r[0] if r else donor
    # a body's id is its car's (the RX-7: 424) or its car's x 1000 (the Stratos: 458000), and the new body's follows the
    # donor's: given the plain id under a x 1000 donor the game hung loading the car (the Stratos, the Impreza 22B)
    new_body = new * 1000 if body == donor * 1000 else new

    def remap(table, col, v):
        if not isinstance(v, int) or isinstance(v, bool):
            return v
        c = col.lower()
        if c in CAR_COLUMNS or (c == "id" and table in CAR_ID_TABLES):
            return new if v == donor else v
        if c == "carbodyid" or (c == "id" and table == "Data_CarBody"):
            return new_body if v == body else v
        if "id" in c and v >= 1000 and v // 1000 in (donor, body):
            return new * 1000 + v % 1000
        return v
    out = {}
    for (t,) in db.execute("select name from sqlite_master where type = 'table' order by name").fetchall():
        if t in skip:
            continue
        cols = columns(db, t)
        # the engine's and drivetrain's own tables are engine_rows' (their ids are the engine's or drivetrain's x 1000,
        # which can equal the car's or the body's: a clutch row copied here too was a duplicate). Every other table
        # stays: List_AeroPhysics has neither a car nor a body column, but the wing rows name its rows by the car's id
        # x 1000 + n (the Stratos, the Impreza 22B): left out, the game hung loading the car
        lower = {c.lower() for c in cols}
        if lower & {"engineid", "drivetrainid"} and not lower & CAR_COLUMNS:
            continue
        rows = []
        for row in db.execute("select * from [%s]" % t):
            nr = [remap(t, c, v) for c, v in zip(cols, row)]
            if nr != list(row):
                rows.append(dict(zip(cols, nr)))
        if rows:
            fresh_keys(db, t, cols[0], rows)
            out[t] = rows
    return out


def _unique(db, table, col):
    info = db.execute("pragma table_info([%s])" % table).fetchall()
    if any(r[1] == col and r[5] for r in info) and sum(1 for r in info if r[5]) == 1:
        return True
    for idx in db.execute("pragma index_list([%s])" % table).fetchall():
        if idx[2] and [r[2] for r in db.execute("pragma index_info([%s])" % idx[1])] == [col]:
            return True
    return False


def fresh_keys(db, table, key, rows):
    """A copied row whose unique key is not made from the car's id (Combo_Engines.EngineComboID)
    keeps the donor's: it gets the table's next free one instead."""
    if not _unique(db, table, key):
        return
    taken = {r[0] for r in db.execute("select [%s] from [%s]" % (key, table))}
    ints = [v for v in taken if isinstance(v, int)]
    nxt = max(ints + [0]) + 1
    for r in rows:
        if r[key] in taken and isinstance(r[key], int):
            r[key], nxt = nxt, nxt + 1
        taken.add(r[key])


def fh1_make(db, make, fh1_strings=None):
    """(ID, ManufacturerCode) of FH1's make named make: IconPathBase holds the plain name."""
    for mid, code, base in db.execute("select ID, ManufacturerCode, IconPathBase from List_CarMake"):
        if str(base or "").lower() == make.lower():
            return mid, code
    raise Fm1ImportError("FH1 has no make %s (#930: FM1's makes FH1 lacks are not handled yet)" % make)


def colour_class(db, rgb):
    """The ColorClassId of FH1's nearest paint (its Combo_Colors), for the paint menu's groups."""
    best = None
    r, g, b = rgb
    for packed, cls in db.execute("select RGB, ColorClassId from Combo_Colors"):
        pr, pg, pb = (packed >> 16) & 255, (packed >> 8) & 255, packed & 255
        d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
        if best is None or d < best[0]:
            best = (d, cls)
    return best[1] if best else 1


def part_positions(rows, new, steering_wheel, found):
    """CarPartPositions from FM1's car (the list's z points forward, the model's front is -z):
    0/1 the head lamps, 2/3 the tail lamps (left, right), 4 the steering wheel's centre, 9 kept
    where it sits from the steering wheel in the donor (mirrored when the wheel changes side).
    5-8 (the wheels) stay the donor's: the model is placed on them."""
    pos = {r["PartId"]: r for r in rows}

    def table(p):
        return [p[0], p[1], -p[2]]
    for kind, base in (("head", 0), ("tail", 2)):
        for side, p in found.get(kind, []):
            r = pos.get(base + side - 1)
            if r:
                r["PartPosX"], r["PartPosY"], r["PartPosZ"] = table(p)
    if steering_wheel and 4 in pos:
        old = [pos[4]["PartPosX"], pos[4]["PartPosY"], pos[4]["PartPosZ"]]
        w = table(steering_wheel)
        pos[4]["PartPosX"], pos[4]["PartPosY"], pos[4]["PartPosZ"] = w
        if 9 in pos:
            d = [pos[9]["PartPosX"] - old[0], pos[9]["PartPosY"] - old[1], pos[9]["PartPosZ"] - old[2]]
            if (old[0] < 0) != (w[0] < 0):
                d[0] = -d[0]
            pos[9]["PartPosX"], pos[9]["PartPosY"], pos[9]["PartPosZ"] = (w[0] + d[0], w[1] + d[1], w[2] + d[2])


def car_rows(db, donor, new, media, info, refs, steering_wheel=None, found=None, physics=None):
    """The FM1 car's rows: the donor's cloned, with FM1's id, MediaName, make, year, names (refs:
    DisplayName and ModelShort references), paints, part places and physics (fm1_physics)."""
    rows = clone_rows(db, donor, new)
    car = rows["Data_Car"][0]
    make_id, _ = fh1_make(db, info["make"])
    donor_media = car["MediaName"]
    car.update(Year=info["year"], MakeID=make_id, MediaName=media, DisplayName=refs[0], ModelShort=refs[1])
    for k, v in list(car.items()):
        if isinstance(v, str) and k != "MediaName" and re.search(r"(?i)thumbnail_%d\b" % donor, v):
            car[k] = re.sub(r"(?i)(thumbnail_)%d\b" % donor, r"\g<1>%d" % new, v)
    if info["colours"] and "Combo_Colors" in rows:
        tpl = rows["Combo_Colors"][0]
        rows["Combo_Colors"] = [dict(tpl, ID=new * 1000 + k + 1, Ordinal=new, Sequence=k + 1, Metallic=m,
                                     RGB=r << 16 | g << 8 | b, ColorClassId=colour_class(db, (r, g, b)))
                                for k, (r, g, b, m) in enumerate(info["colours"])]
    if "CarPartPositions" in rows:
        part_positions(rows["CarPartPositions"], new, steering_wheel, found or {})
    if physics:
        apply_physics(rows, physics)
    return rows, donor_media


# ============================================================================= physics

# FM1's physics (Cars.ca2 cars/<car>/physics/*.xml) name their values as FH1's database does.
# The car's own rows take FM1's (the engine and drivetrain are still the donor's):
#   weight/l0-l3.xml        List_UpgradeCarBodyWeight levels 0-3: Mass, CMHeight, CMBackFront,
#                           CMLeftRight, BlockDimX/Y/Z; Data_Car CurbWeight (mass / 100) and
#                           WeightDistribution (CM_BackFront, the front's share)
#   suspension/l0, l2, l3   the springs, dampers and anti-roll bars of FH1's levels 1-3 (stock,
#                           sport, race; FM1's l1, street, has no FH1 level; FH1's level 4, the
#                           rally suspension, stays the donor's). Ride heights stay the donor's: the
#                           model sits on the donor's wheels and tires
#   aero/body.xml           Data_Car BodyAero* and TireAeroHack*
#   nonupgradedata.xml      Data_Car SteerMaxAngle (the steering's rates and FH1's FixListing
#                           handling corrections stay the donor's)
SPRING_LEVELS = {1: "l0", 2: "l2", 3: "l3"}
SPRING_KEEP = {"DefRideHeight", "MinRideHeight", "MaxRideHeight", "MaxCompressHeight"}
AERO = {"LongitudinalDrag": "BodyAeroLongitudinalDrag", "VerticalDrag": "BodyAeroVerticalDrag",
        "LateralDragFront": "BodyAeroLateralDragFront", "LateralDragRear": "BodyAeroLateralDragRear",
        "ForwardDownforceFront": "BodyAeroForwardDownforceFront", "ForwardDownforceRear": "BodyAeroForwardDownforceRear",
        "AngleZeroDownforce": "BodyAeroAngleZeroDownforce", "WIForceScale": "BodyAeroWIForceScale"}


def _attrs(text, tag):
    m = re.search(r"<%s\b([^>]*)/?>" % tag, text)
    if not m:
        return {}
    out = {}
    for k, v in re.findall(r'(\w+)="([^"]*)"', m.group(1)):
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v.lower() == "true" if v.lower() in ("true", "false") else v
    return out


def fm1_physics(fm1_dir, car):
    """{"weight": {level: attrs}, "suspension": {"l0": {"Front": attrs, "Rear": attrs}...},
    "aero": attrs, "tire_aero": attrs, "steering": attrs} of an FM1 car (Cars.ca2)."""
    cab = fm1_cars.Cab(os.path.join(fm1_dir, "media", "cabs", "Cars.ca2"))

    def read(rel):
        key = "cars\\%s\\physics\\%s" % (car.lower(), rel)
        return cab.read(key).decode("latin-1") if key in cab.files else None
    out = {"weight": {}, "suspension": {}}
    for k in range(4):
        t = read("weight\\l%d.xml" % k)
        if t:
            out["weight"][k] = _attrs(t, "Weight")
        t = read("suspension\\l%d.xml" % k)
        if t:
            out["suspension"]["l%d" % k] = {"Front": _attrs(t, "Front"), "Rear": _attrs(t, "Rear")}
    t = read("aero\\body.xml")
    if t:
        out["aero"], out["tire_aero"] = _attrs(t, "Body"), _attrs(t, "TireAeroHack")
    t = read("nonupgradedata.xml")
    if t:
        out["steering"] = _attrs(t, "Steering")
    return out


def apply_physics(rows, p):
    car = rows["Data_Car"][0]
    w0 = p.get("weight", {}).get(0)
    if w0:
        car["CurbWeight"] = w0["Mass"] / 100.0
        car["WeightDistribution"] = w0["CM_BackFront"]
    for r in rows.get("List_UpgradeCarBodyWeight", []):
        w = p.get("weight", {}).get(r["Level"])
        if w:
            r.update(Mass=w["Mass"], InitialMass=w0["Mass"] if w0 else r["InitialMass"], CMHeight=w["CM_Height"],
                     CMBackFront=w["CM_BackFront"], CMLeftRight=w["CM_LeftRight"],
                     BlockDimX=w["BlockDim_x"], BlockDimY=w["BlockDim_y"], BlockDimZ=w["BlockDim_z"])
    springs = {r["SpringDamperPhysicsID"]: r for r in rows.get("List_SpringDamperPhysics", [])}
    sways = {r["AntiSwayPhysicsID"]: r for r in rows.get("List_AntiSwayPhysics", [])}
    for up in rows.get("List_UpgradeSpringDamper", []):
        s = p.get("suspension", {}).get(SPRING_LEVELS.get(up["Level"]))
        if not s:
            continue
        for end, col in (("Front", "FrontSpringDamperPhysicsID"), ("Rear", "RearSpringDamperPhysicsID")):
            r, a = springs.get(up[col]), s[end]
            if r is None:
                continue
            for k, v in a.items():
                if k in r and k not in SPRING_KEEP and not k.endswith("SwaybarStiffness") and k != "SwaybarDamping":
                    r[k] = v
            if "CamberChangeRatio" in a:
                r["CamberChangeRatioCompression"] = r["CamberChangeRatioExpansion"] = a["CamberChangeRatio"]
    for table, end in (("List_UpgradeAntiSwayFront", "Front"), ("List_UpgradeAntiSwayRear", "Rear")):
        for up in rows.get(table, []):
            s = p.get("suspension", {}).get(SPRING_LEVELS.get(up["Level"]))
            r = sways.get(up["AntiSwayPhysicsID"])
            if s and r is not None:
                for k in ("DefSwaybarStiffness", "MinSwaybarStiffness", "MaxSwaybarStiffness", "SwaybarDamping"):
                    if k in s[end]:
                        r[k] = s[end][k]
    for k, col in AERO.items():
        if k in p.get("aero", {}):
            car[col] = p["aero"][k]
    # FH1's "no downforce" is front 0, rear -1 (24 of its cars; none has rear 0)
    if car.get("BodyAeroForwardDownforceRear") is not None and car["BodyAeroForwardDownforceRear"] <= 0:
        car["BodyAeroForwardDownforceRear"] = -1.0
    if car.get("BodyAeroForwardDownforceFront") is not None and car["BodyAeroForwardDownforceFront"] < 0:
        car["BodyAeroForwardDownforceFront"] = 0.0
    ta = p.get("tire_aero", {})
    for k in ("ShouldApply", "MinSpringLoad", "MaxSpringLoad"):
        if k in ta:
            car["TireAeroHack" + k] = int(ta[k]) if k == "ShouldApply" else ta[k]
    if "MaxAngle" in p.get("steering", {}):
        car["SteerMaxAngle"] = p["steering"]["MaxAngle"]


# ============================================================================= the engine

# The car gets an engine and a drivetrain of its own (FH1 keys every engine part table by EngineID
# and every drivetrain part table by DrivetrainID, rows EngineID x 1000 + level): the donor's rows
# cloned under new ids, then FM1's engine put in. Calibrated on cars both games have (FM1's
# nissansilvia and mazda_rx7 against FH1's NIS_SilviaSpecR_00 and MAZ_RX7_97):
#   enginetuning/l0-l3      List_UpgradeEngineCamshaft levels 0-3 (FH1's S15 stock row is FM1's
#                           exactly): RedlineRPM, StallRPM, StartRPM, RedlineWearSpeed, MaxRPM,
#                           NumCurveValuesToRead; each level's own List_TorqueCurve: FM1's
#                           FullThrottle values, the last one FM1's ZeroThrottle there (FH1's -2.63),
#                           TorqueScale FM1's FullThrottle TorqueScale times the parts FM1 fits as
#                           stock (the MR2's intake/exhaust is at l1; FH1's stock part rows are 1.0)
#   turbo/l0, l1, l3        the stock turbo (FH1 level 1) and its upgrades (2, 3), as FH1's S15:
#                           MaxScale, MinScale, RobScale FM1's; MomentInertia x 0.31, PowerMaxScale
#                           x 0.75 (FH1's S15 / FM1's S15); PowerMinScale 10 (every FH1 turbo)
#   base.xml                Data_Engine EngineMass-kg; Inertia, split between Data_Engine and the
#                           flywheel levels as the donor's
#   transmission/l0-l2      FH1's stock gearbox level and the next two (inertia, shift time,
#                           final drive, ratios); FH1's race and rally gearboxes (generic: the same
#                           ratios on the S15 and the RX-7) stay the donor's
#   supercharger/l0, l1, l3 the stock supercharger and its upgrades, as the turbo: ZeroRPMScale,
#                           RedlineRPMScale, RobScale FM1's (FH1's Ford GT's 1.408 / 0.928 are
#                           FM1's 1.40 / 0.929)
#   model.dat               engine placement and drive type
# The aspiration is what FM1's engine fits as stock: its turbo or its supercharger switched on
# at its StockLevel, else natural (a naturally aspirated car gets no forced induction rows, as
# most of FH1's: no conversions). FM1 has one turbo part: a twin turbo is ENGINE_SPECS'
# ("aspiration": "twin"), as a centrifugal supercharger ("csc"; positive displacement, FH1's DSC,
# otherwise).
#   intakeexhaust, fuelignition, intercooler l0-l3: the bolt-on parts' gains (PART_TABLES)
# The diffs, clutch, driveline and the bolt-on parts' prices and masses stay the donor's: FH1
# retuned FM1's clutches and diffs (the S15's clutch 830 Nm, 0.15 s in, against FM1's 1000 Nm,
# 0.09 s), and its prices and masses are relative to stock. FM1's brake levels are the generic
# GameFrictionScaleBraking 1.00, 1.01, 1.03, 1.05 FH1's cars have too, and FH1 retuned FM1's
# aero kits (its S15 race wing 34 / 91 kg of downforce, FM1's 25 / 75): the donor's.
# What FM1 does not hold (the layout, cylinders, displacement, compression, boost, a name) and the
# FH1 sound that suits the engine (a car's _CMT and _ET tuning) come from ENGINE_SPECS.
FM1_DRIVE = {1: 3, 2: 1, 3: 2}  # FM1's AWD, FWD, RWD -> FH1's DriveTypeID
ASPIRATIONS = {"natural": (1, None), "turbo": (2, "List_UpgradeEngineTurboSingle"),
               "twin": (3, "List_UpgradeEngineTurboTwin"), "supercharger": (5, "List_UpgradeEngineDSC"),
               "csc": (6, "List_UpgradeEngineCSC")}  # -> (List_Aspiration id, FH1's table)
FORCED_TABLES = ("List_UpgradeEngineTurboSingle", "List_UpgradeEngineTurboTwin", "List_UpgradeEngineTurboQuad",
                 "List_UpgradeEngineDSC", "List_UpgradeEngineCSC")
FORCED_TEMPLATE = {"ManufacturerID": 0, "PartsStringID": 0, "MassDiff": 0.0, "WeightDistDiff": 0.0, "DragScale": 1.0,
                   "WindInstabilityScale": 1.0, "Price": 0, "TorqueDropOffRPM0": 9000.0, "TorqueDropOffRPM1": 15000.0,
                   "TorqueDropOffScale0": 1.0, "TorqueDropOffScale1": 0.75}  # FH1's, on every forced row
CYLINDERS = {4: 1, 5: 2, 6: 3, 8: 4, 10: 5, 12: 6, 16: 7}  # count -> FH1's CylinderID
CONFIGS = {"V": 1, "W": 2, "I": 3, "R": 4, "F": 5}  # List_EngineConfig
FORCED_LEVELS = {1: 0, 2: 1, 3: 3}  # FH1's level -> FM1's level above its stock one
TURBO_INERTIA, TURBO_POWER_MAX, TURBO_POWER_MIN = 0.31, 0.75, 10.0
ENGINE_SPECS = {
    # the SW20 MR2 Turbo's 3S-GTE (third generation, 1994-): 1,998 cc, 8.5:1, 0.9 bar. FH1 has no
    # MR2 Turbo, but its sound tuning still holds FM4's TOY_MR2GT_95: the MR2's own turbo intake
    # bank (4T3_MR2), its engine bay and an SR20 turbo exhaust
    "bordermr2": {"config": "I", "cylinders": 4, "displacement": 1998, "compression": 8.5, "boost": 0.9,
                  "name": "I4T   -   Toyota 3S-GTE", "sound": "TOY_MR2GT_95"},
}


# ============================================================================= ratings

# The car list's bars and PI (Data_Car *Rating, PerformanceIndex, ClassID) are numbers FH1's
# tools worked out for each car; the game shows them as they are. The game also works them out
# itself, live, for the car as it is (the garage header's PI, the stats on the upgrade screens),
# and those are what the car's own rows should hold. Nothing outside the game computes them, so
# RATINGS holds them as read from the game (Custom Upgrade, a part list's STATS, stock parts).
# PerformanceIndex is the shown PI through CarClasses: each class's MaxDisplayPerformanceIndex
# (B 500) is its MaxPerformanceIndex (B 0.6505), linear in between (the RX-7's 0.6213 shows 445).
RATINGS = {
    # ReImagined v0.37, the import of 2026-11-16 (FM1's chassis, engine and gearbox)
    "bordermr2": {"pi": 317, "SpeedRating": 3.8, "HandlingRating": 8.0, "AccelerationRating": 6.0,
                  "LaunchRating": 6.7, "BrakingRating": 7.7},
    "hondas2000": {"pi": 339, "SpeedRating": 3.9, "HandlingRating": 8.2, "AccelerationRating": 6.2,
                   "LaunchRating": 5.9, "BrakingRating": 8.2},
}
# FM1's forzadb/cardb/gamecars.dat: 176-byte rows starting with the car's ordinal twice; these
# fields are the figures FH1 keeps in Data_Car's Sim* columns, in the same units: the engine's
# (for the RX-7 and the S15 FH1's are FM1's, or within 3 %) and the car's measured performance
# (seconds, m/s, metres, g; the RX-7's 0-60 mph 5.5 s in FM1 and 5.67 s in FH1, the F50's lateral
# grip at 120 mph 1.08 and 1.15 g, its top speed 89.0 and 88.2 m/s): offset -> column
GAMECARS_ROW = 176
GAMECARS_FIGURES = {12: "SimPeakPower", 16: "SimPeakAngVel", 20: "SimPeakTorque", 24: "SimPeakTorqueAngVel",
                    28: "SimRedlineAngVel", 48: "SimTimeTo60MPH", 52: "SimTimeTo100MPH", 56: "SimTimeQuarterMile",
                    60: "SimSpeedQuarterMile", 64: "SimTopSpeed", 68: "SimBrakeDistance100MPH",
                    72: "SimBrakeDistance60MPH", 76: "SimLatGees60MPH", 80: "SimLatGees120MPH"}


def display_pi(db, shown):
    """(PerformanceIndex, ClassID) of a PI the game shows (100-999). Aimed a quarter below it:
    the exact value (0.548945 for 317) shows one more (318), so the game rounds up."""
    lo_pi, lo_shown = 0.0, 0
    shown -= 0.25
    for cid, max_pi, max_shown in db.execute(
            "select Id, MaxPerformanceIndex, MaxDisplayPerformanceIndex from CarClasses order by MaxDisplayPerformanceIndex, Id"):
        if shown <= max_shown:
            return lo_pi + (shown - lo_shown) / float(max_shown - lo_shown) * (max_pi - lo_pi), cid
        lo_pi, lo_shown = max_pi, max_shown
    raise Fm1ImportError("PI %d is above FH1's classes" % shown)


def fm1_figures(fm1_dir, ordinal):
    """{Data_Car column: value} of FM1's gamecars.dat row for the car (GAMECARS_FIGURES)."""
    mega = fm1_cars.Cab(os.path.join(fm1_dir, "media", "cabs", "Mega.ca2"))
    g = mega.read("forzadb/cardb/gamecars.dat")
    for at in range(len(g) % GAMECARS_ROW, len(g) - GAMECARS_ROW + 1, GAMECARS_ROW):
        if struct.unpack_from("<2I", g, at) == (ordinal, ordinal):
            return {col: struct.unpack_from("<f", g, at + off)[0] for off, col in GAMECARS_FIGURES.items()}
    return {}


def apply_ratings(db, car, figures, ratings):
    """The car's Data_Car row: FM1's engine figures; the ratings, PI and class read from the
    game (RATINGS) when there are some. Returns whether there were."""
    car.update(figures)
    if not ratings:
        return False
    pi, cid = display_pi(db, ratings["pi"])
    car.update({k: v for k, v in ratings.items() if k.endswith("Rating")}, PerformanceIndex=pi, ClassID=cid)
    return True


def donor_spec(db, donor_id, donor_media):
    """ENGINE_SPECS' fields from the donor's stock engine (its layout, cylinders, displacement,
    compression, boost and name) and the donor's sound: right when the donor is the same car
    (FH1's HON_S2000_03 for FM1's HondaS2000), and the default for a car ENGINE_SPECS lacks."""
    r = db.execute("""select e.ConfigID, e.CylinderID, e.Compression, e.[StockBoost-bar], e.EngineName, c.Displacement
                      from Data_Car c join List_UpgradeEngine u on u.Ordinal = c.Id and u.IsStock = 1
                      join Data_Engine e on e.EngineID = u.EngineID where c.Id = ?""", (donor_id,)).fetchone()
    if r is None:
        raise Fm1ImportError("the donor %d has no stock engine" % donor_id)
    config = {v: k for k, v in CONFIGS.items()}.get(r[0], "I")
    cylinders = {v: k for k, v in CYLINDERS.items()}.get(r[1], 4)
    return {"config": config, "cylinders": cylinders, "compression": r[2], "boost": r[3] or 0.0,
            "name": r[4] or "", "displacement": r[5], "sound": donor_media, "from_donor": True}


def pick_sound(db, config, cylinders, aspiration, make_id=None, year=None, have=None):
    """The MediaName of an FH1 car to take the sound tuning (_CMT, _ET, _DSPTuning) from: its stock
    engine has the same layout, cylinder count and aspiration (FH1's ids), the same make first,
    then the nearest year. have(media): whether its sound files exist (FM4's leftovers count:
    TOY_MR2GT_95 has sound but no car row). None when no FH1 engine matches."""
    best = None
    for cid, media, cyear, cmake in db.execute(
            """select c.Id, c.MediaName, c.Year, c.MakeID from Data_Car c
               join List_UpgradeEngine u on u.Ordinal = c.Id and u.IsStock = 1
               join Data_Engine e on e.EngineID = u.EngineID
               where e.ConfigID = ? and e.CylinderID = ? and e.AspirationID_Stock = ? and c.Id < 55000""",
            (CONFIGS[config], CYLINDERS[cylinders], aspiration)):
        if have and not have(media):
            continue
        score = (0 if cmake == make_id else 1, abs((cyear or 0) - (year or cyear or 0)), cid)
        if best is None or score < best[0]:
            best = (score, media)
    return best[1] if best else None


def _values(attrs):
    return [attrs["v%d" % i] for i in range(len(attrs)) if "v%d" % i in attrs]


def fm1_engine(fm1_dir, car):
    """FM1's engine and gearbox of a car: {"model": {drive, placement, cylinders, aspiration},
    "base", "tuning" {level: {data, max_rpm, n, full_scale, full, zero}}, "turbo" {level: attrs},
    "parts" {name: {level: scale}}, "stock" {part: level}, "transmission" {level: {data, ratios}}}."""
    cab = fm1_cars.Cab(os.path.join(fm1_dir, "media", "cabs", "Cars.ca2"))
    mega = fm1_cars.Cab(os.path.join(fm1_dir, "media", "cabs", "Mega.ca2"))

    def read(key):
        key = key.replace("/", "\\").lower()
        return cab.read(key).decode("latin-1") if key in cab.files else None
    tool = read("cars/%s/tooldata.xml" % car) or ""
    m = re.search(r'<Engines>.*?<E0\b[^>]*File="([^"]+)"', tool, re.S)
    eng = (m.group(1) if m else car).lower()
    out = {"tuning": {}, "turbo": {}, "parts": {}, "stock": {}, "transmission": {}}
    info = fm1_info(fm1_dir, cab, car)
    model = mega.read("forzadb/cardb/model.dat")
    row = next(model[o:o + 104] for o in range(0, len(model) - 103, 104)
               if struct.unpack_from("<I", model, o)[0] == info["ordinal"])
    # model.dat from 52: drive type (1 AWD, 2 FWD, 3 RWD), engine placement (1 front, 2 mid,
    # 3 rear); then two fields that are not the engine's: 64 is 1 for production cars, 2 for
    # tuner cars, 3 for race cars, 60 runs 1-6 with no link to the engine (the Viper SRT-10: 1)
    drive, placement = struct.unpack_from("<2I", row, 52)
    out["model"] = {"drive": drive, "placement": placement}
    base = read("engines/%s/physics/base.xml" % eng)
    if base is None:
        raise Fm1ImportError("FM1 has no engine %s" % eng)
    out["base"] = _attrs(base, "Data")
    for k in range(4):
        t = read("engines/%s/physics/enginetuning/l%d.xml" % (eng, k))
        if t:
            curves = _attrs(t, "TorqueCurves")
            full, zero = _attrs(t, "FullThrottle"), _attrs(t, "ZeroThrottle")
            out["tuning"][k] = {"data": _attrs(t, "Data"), "max_rpm": curves["MaxRPM"],
                                "n": int(curves["NumCurveValuesToRead"]), "full_scale": full["TorqueScale"],
                                "full": _values(full), "zero": _values(zero)}
        t = read("engines/%s/physics/turbo/l%d.xml" % (eng, k))
        if t:
            out["turbo"]["l%d" % k] = _attrs(t, "Turbo")
        t = read("engines/%s/physics/supercharger/l%d.xml" % (eng, k))
        if t:
            out.setdefault("supercharger", {})["l%d" % k] = _attrs(t, "Supercharger")
        for part, tag, key in (("intakeexhaust", "IntakeExhaust", "TorqueScale"),
                               ("fuelignition", "FuelIgnition", "TorqueScale"),
                               ("intercooler", "Intercooler", "MaxScaleScale")):
            t = read("engines/%s/physics/%s/l%d.xml" % (eng, part, k))
            if t:
                out["parts"].setdefault(part, {})[k] = _attrs(t, tag).get(key, 1.0)
        t = read("cars/%s/physics/transmission/l%d.xml" % (car, k))
        if t:
            ratios = _values(_attrs(t, "GearRatios"))
            out["transmission"][k] = {"data": _attrs(t, "Data"), "ratios": ratios}
    etool = read("engines/%s/tooldata.xml" % eng) or ""
    for part, level in re.findall(r'<(\w+) StockLevel="(\d)"', etool):
        out["stock"][part.lower()] = int(level)
    out["aspiration"] = fm1_aspiration(out)
    return out


# FM1's bolt-on engine parts against FH1's (both are torque multipliers per level, FH1's per car):
# FM1's intake-and-exhaust part is FH1's intake and exhaust, its fuel-and-ignition part FH1's fuel
# system and ignition, its intercooler FH1's (the turbo's MaxScale). FM1's gain at its stock level
# is in the torque curve already (the fold above), so FH1's rows get FM1's gains relative to it,
# FH1's levels above stock spread evenly up to FM1's top level (geometric in between: the MR2's
# intake and exhaust, stock at FM1's level 1, have two FM1 levels above for FH1's three). A pair's gain at
# a level is shared between the two parts in the donor's proportion (the product of the two is
# FM1's; FH1's S15 at level 3 has 1.085 x 1.137 for intake and exhaust where FM1's is 1.126).
# FH1's other parts (valves, pistons, displacement, oil cooling, flywheel) have no FM1 counterpart.
PART_TABLES = {"intakeexhaust": (("List_UpgradeEngineIntake", "List_UpgradeEngineExhaust"), "TorqueScale"),
               "fuelignition": (("List_UpgradeEngineFuelSystem", "List_UpgradeEngineIgnition"), "TorqueScale"),
               "intercooler": (("List_UpgradeEngineIntercooler",), "MaxScaleScale")}


def _gain_at(gains, x):
    """FM1's part gain at a fractional level x: geometric between its levels either side."""
    lo, hi = int(math.floor(x)), int(math.ceil(x))
    below = [k for k in gains if k <= lo]
    a = (gains[max(below)] if below else 1.0) or 1.0
    b = gains.get(hi) or a
    return a * (b / a) ** (x - lo)


def apply_part_gains(erows, eng):
    """FM1's bolt-on part gains (PART_TABLES) into the engine's cloned part rows."""
    for part, (tables, col) in PART_TABLES.items():
        gains = eng["parts"].get(part)
        if not gains:
            continue
        stock = eng["stock"].get(part, 0)
        base, top = gains.get(stock, 1.0) or 1.0, max(gains)
        by_level = {}
        for t in tables:
            rs = sorted(erows.get(t, []), key=lambda r: r["Level"])
            s = next((i for i, r in enumerate(rs) if r["IsStock"]), 0)
            for r in rs[:s + 1]:
                r[col] = 1.0
            for r in rs[s + 1:]:
                by_level.setdefault(r["Level"], []).append(r)
        steps = len(by_level)
        for step, level in enumerate(sorted(by_level), 1):
            target = _gain_at(gains, stock + step * (top - stock) / float(steps)) / base
            rs = by_level[level]
            have = 1.0
            for r in rs:
                have *= r[col] or 1.0
            for r in rs:
                if have > 1.0001 and target > 0:
                    r[col] = (r[col] or 1.0) ** (math.log(target) / math.log(have))
                else:
                    r[col] = target ** (1.0 / len(rs))


def _on(attrs):
    return bool(attrs) and str(attrs.get("OnOff", "OFF")).upper() == "ON"


def fm1_aspiration(eng):
    """"turbo", "supercharger" or "natural": what FM1 fits as stock (the turbo or the supercharger
    switched on at its StockLevel; FM1 has one turbo part, a twin turbo is ENGINE_SPECS')."""
    if _on(eng["turbo"].get("l%d" % eng["stock"].get("turbo", 0))):
        return "turbo"
    if _on(eng.get("supercharger", {}).get("l%d" % eng["stock"].get("supercharger", 0))):
        return "supercharger"
    return "natural"


def _free_id(db, table, key, want):
    """The first id from `want` free in `table` whose rows (id x 1000 + n) are free too in every
    table keyed by `key` (an FM4 car's or an earlier import's rows can hold that range even where
    `table` has no such id: the Stratos clashed on List_UpgradeDrivetrainClutch.Id)."""
    taken = {r[0] for r in db.execute("select [%s] from [%s]" % (key, table))}
    for (t,) in db.execute("select name from sqlite_master where type = 'table'").fetchall():
        cols = columns(db, t)
        if "Id" in cols and any(c.lower() == key.lower() for c in cols):
            taken.update(r[0] // 1000 for r in db.execute("select Id from [%s]" % t) if isinstance(r[0], int))
    while want in taken:
        want += 1
    return want


def _clone_keyed(db, key, donor, new):
    """{table: [rows]}: every row whose key column (EngineID, DrivetrainID) is the donor's, under
    the new id; an Id or TorqueCurveID column made from it (donor x 1000 + n) follows."""
    out = {}
    for (t,) in db.execute("select name from sqlite_master where type = 'table' order by name").fetchall():
        cols = columns(db, t)
        col = next((c for c in cols if c.lower() == key.lower()), None)
        if not col or t in ("List_UpgradeEngine", "List_UpgradeDrivetrain", "Combo_Engines"):
            continue
        rows = []
        for row in db.execute("select * from [%s] where [%s] = ?" % (t, col), (donor,)):
            r = dict(zip(cols, row))
            r[col] = new
            if "Id" in r and isinstance(r["Id"], int) and r["Id"] // 1000 == donor:
                r["Id"] = new * 1000 + r["Id"] % 1000
            rows.append(r)
        if rows:
            out[t] = rows
    return out


def engine_rows(db, rows, eng, spec, new, media):
    """FM1's engine and drivetrain into the car's rows (see above). Returns (engine id, drivetrain id)."""
    kind = spec.get("aspiration") or eng.get("aspiration", "natural")
    if kind == "turbo" and eng.get("aspiration") != "turbo" or kind in ("twin",) and eng.get("aspiration") != "turbo" \
            or kind in ("supercharger", "csc") and eng.get("aspiration") != "supercharger":
        raise Fm1ImportError("ENGINE_SPECS says %s, FM1's engine fits %s as stock" % (kind, eng.get("aspiration")))
    asp, forced_table = ASPIRATIONS[kind]
    car = rows["Data_Car"][0]
    stock_engine = next(r for r in rows["List_UpgradeEngine"] if r["IsStock"])
    stock_drive = next(r for r in rows["List_UpgradeDrivetrain"] if r["IsStock"])
    donor_eid, donor_did = stock_engine["EngineID"], stock_drive["DrivetrainID"]
    eid = _free_id(db, "Data_Engine", "EngineID", new)
    did = _free_id(db, "Data_Drivetrain", "DrivetrainID", new)
    erows, drows = _clone_keyed(db, "EngineID", donor_eid, eid), _clone_keyed(db, "DrivetrainID", donor_did, did)
    for r in rows["List_UpgradeEngine"]:
        r["EngineID"] = eid
    for r in rows.get("Combo_Engines", []):
        r["EngineID"] = eid

    # the engine itself
    m, base = eng["model"], eng["base"]
    e = erows["Data_Engine"][0]
    fly = sorted(erows.get("List_UpgradeEngineFlywheel", []), key=lambda r: r["Level"])
    whole = e["MomentInertia"] + (fly[0]["MomentInertia"] if fly else 0.0)
    k = base["Inertia"] / whole if whole else 1.0
    e.update({"EngineMass-kg": base["Mass"], "MediaName": media, "ConfigID": CONFIGS[spec["config"]],
              "CylinderID": CYLINDERS[spec["cylinders"]], "Compression": spec["compression"],
              "AspirationID_Stock": asp, "StockBoost-bar": spec.get("boost", 0.0), "EngineName": spec["name"],
              "MomentInertia": e["MomentInertia"] * k, "Rotary": int(spec["config"] == "R")})
    for r in fly:
        r["MomentInertia"] *= k

    # torque curves and the camshaft levels
    stock = eng["stock"]
    fold = 1.0
    for part in ("intakeexhaust", "fuelignition"):
        fold *= eng["parts"].get(part, {}).get(stock.get(part, 0), 1.0)
    old_curves = {r["TorqueCurveFullThrottleID"] for r in erows.get("List_UpgradeEngineCamshaft", [])}
    ids = ",".join(str(int(c)) for c in old_curves if c is not None) or "0"
    found = db.execute("select * from List_TorqueCurve where TorqueCurveID in (%s) limit 1" % ids).fetchone()
    if found is None:
        raise Fm1ImportError("the donor's engine %d has no torque curve" % donor_eid)
    tpl = dict(zip(columns(db, "List_TorqueCurve"), found))
    vcols = [c for c in tpl if re.fullmatch(r"v\d+", c)]
    curves = []
    # FH1's cam levels: 0-3 on some engines (the RX-7's), stock 1 and upgrades 2, 3 on most: FM1's l0 for the stock
    # one, then its l1-l3, or l1 and l3 for two upgrades (as the turbo)
    cams = sorted(erows.get("List_UpgradeEngineCamshaft", []), key=lambda r: r["Level"])
    first = next((i for i, r in enumerate(cams) if r["IsStock"]), 0)
    above = cams[first + 1:]
    fm1_levels = dict(zip([id(r) for r in above], [1, 2, 3] if len(above) >= 3 else [1, 3][:len(above)]))
    if cams:
        fm1_levels[id(cams[first])] = 0
    for r in cams[:first]:
        fm1_levels[id(r)] = 0
    for r in cams:
        t = eng["tuning"].get(fm1_levels.get(id(r)))
        if not t:
            continue
        d, n = t["data"], t["n"]
        if n > len(vcols):
            raise Fm1ImportError("FM1's torque curve has %d values, FH1's table %d" % (n, len(vcols)))
        cid = eid * 1000 + r["Level"]
        r.update(RedlineRPM=d["RedlineRPM"], StallRPM=d["StallRPM"], StartRPM=d["StartRPM"],
                 RedlineWearSpeed=d["RedlineWearSpeed"], TorqueCurveMaxRPM=t["max_rpm"], NumRPMEntriesArray=n,
                 TorqueCurveFullThrottleID=cid)
        values = t["full"][:n - 1] + [t["zero"][n - 1]]
        curve = dict(tpl, TorqueCurveID=cid, TorqueScale=t["full_scale"] * fold, NumTorqueValues=n)
        for i, c in enumerate(vcols):
            curve[c] = values[i] if i < n else 0.0
        curves.append(curve)
    erows["List_TorqueCurve"] = curves
    apply_part_gains(erows, eng)

    # forced induction: FM1's turbo or supercharger in its own table; the donor's other kinds left
    # out; a naturally aspirated engine has none (and no intercooler)
    old = {t: erows.pop(t, []) for t in FORCED_TABLES}
    if forced_table is None:
        erows.pop("List_UpgradeEngineIntercooler", None)
    else:
        turbo = kind in ("turbo", "twin")
        src = eng["turbo"] if turbo else eng.get("supercharger", {})
        first = stock.get("turbo" if turbo else "supercharger", 0)
        templates = {r["Level"]: r for rs in old.values() for r in rs}
        cols = columns(db, forced_table)
        ic = eng["parts"].get("intercooler", {}).get(stock.get("intercooler", 0), 1.0)
        forced = []
        for level, up in sorted(FORCED_LEVELS.items()):
            a = src.get("l%d" % (3 if up == 3 else min(3, first + up)))
            if not _on(a):
                continue
            tp = next((r for r in old[forced_table] if r["Level"] == level), None) or templates.get(level) or {}
            r = dict(FORCED_TEMPLATE, **{c: tp[c] for c in cols if c in tp})
            r = {c: r.get(c) for c in cols}
            r.update(Id=eid * 1000 + (0 if level == 1 else level), EngineID=eid, Level=level, IsStock=int(level == 1))
            boost = ic if level == 1 else 1.0
            if turbo:
                r.update(MomentInertia=a["Inertia"] * TURBO_INERTIA, MaxScale=a["MaxScale"] * boost,
                         PowerMaxScale=a["PowerMaxScale"] * TURBO_POWER_MAX, MinScale=a["MinScale"],
                         PowerMinScale=TURBO_POWER_MIN, RobScale=a["RobScale"])
            else:
                r.update(ZeroRPMScale=a["ZeroRPMScale"] * boost, RedlineRPMScale=a["RedlineRPMScale"] * boost,
                         RobScale=a["RobScale"])
            if level == 1:
                r.update(Price=0, MassDiff=0.0, WeightDistDiff=0.0, ManufacturerID=0)
            forced.append(r)
        erows[forced_table] = forced

    # displacement
    for r in erows.get("List_UpgradeEngineDisplacement", []):
        r["Disp"] = round(spec["displacement"] * r["Disp"] / car["Displacement"]) if car.get("Displacement") else spec["displacement"]

    # the gearbox: FH1's stock level and the next two
    trans = sorted(drows.get("List_UpgradeDrivetrainTransmission", []), key=lambda r: r["Level"])
    s = next((i for i, r in enumerate(trans) if r["IsStock"]), 0)
    gears = None
    for k in range(3):
        t = eng["transmission"].get(k)
        if not t or s + k >= len(trans):
            continue
        r = trans[s + k]
        d, ratios = t["data"], [g for g in t["ratios"]]
        if ratios:
            forward = [g for g in ratios[1:] if g > 0]
            # a gearbox row's NumGears counts the reverse gear too (the RX-7's 5-speed: 6, the
            # S15's 6-speed: 7); Data_Car.NumGears counts the forward gears only
            r.update(NumGears=len(forward) + 1, GearRatio0=ratios[0])
            for i in range(1, 9):
                r["GearRatio%d" % i] = forward[i - 1] if i <= len(forward) else -1.0
            if k == 0:
                gears = len(forward)
        r.update(MomentInertia=d["Inertia"], GearShiftTime=d["GearShiftTime"], FinalDriveRatio=d["FinalDriveRatio"])

    # the drive type and engine placement
    dtype, placement = FM1_DRIVE[m["drive"]], m["placement"]
    drows["Data_Drivetrain"][0]["DrivetypeID"] = dtype
    pt = db.execute("select Id from Powertrains where DrivetrainId = ? and EnginePlacementId = ?", (dtype, placement)).fetchone()
    for r in rows["List_UpgradeDrivetrain"]:
        r["DrivetrainID"] = did
        if pt:
            r["PowertrainId"] = pt[0]
    car.update(EngineConfigID=CONFIGS[spec["config"]], CylinderID=CYLINDERS[spec["cylinders"]], AspirationTypeId=asp,
               Displacement=spec["displacement"], EnginePlacementID=placement, DriveTypeID=dtype)
    if pt:
        car["PowertrainID"] = pt[0]
    if gears:
        car["NumGears"] = gears
    for t, rs in list(erows.items()) + list(drows.items()):
        rows.setdefault(t, []).extend(rs)
    return eid, did


# ============================================================================= the wheel

# FM1's wheel into the donor's stock wheel (media/wheels/<name>.zip: one root-3 .carbin, section
# "wheel", LOD 0 and LOD 1-5 in two pools; wheel.xds and wheel_LOD0.xds for the rim, wheel0-3.xds
# for the spin blur). FM1's rim, inner barrel and blur disc go in; the tire stays the donor's (FH1
# draws it with its shared tread textures, which FM1's tire is not mapped for). The wheel is scaled
# to the donor's tire radius, which the car's physics (its tire sizes) still use. The donor's tire
# is made to fit FM1's rim: its sidewall stretched (or shrunk) so its bead meets FM1's, its tread
# and UVs as they were (the MR2's 0.20 m rim in the RX-7's tire, bead 0.236 m, left a gap).
WHEEL_MATERIALS = {11: "rim", 12: "inner_rim", 13: "blur_rim"}
FM1_TIRE = 8


def fit_tire(piece, bead):
    """The donor's tire piece with its sidewall moved radially, the tread radius kept: a radius r
    goes to outer - (outer - r) * (outer - bead) / (outer - its bead)."""
    used = {i for t in piece.tris for i in t}
    radii = [math.hypot(piece.pos[i][1], piece.pos[i][2]) for i in used]
    outer, inner = max(radii), min(radii)
    k = (outer - bead) / (outer - inner) if outer > inner else 1.0
    pos = []
    for x, y, z in piece.pos:
        r = math.hypot(y, z)
        s = (outer - (outer - r) * k) / r if r > 1e-9 else 1.0
        pos.append((x, y * s, z * s))
    return fm1_cars.cb.Piece(piece.section, piece.material, piece.lod, pos, piece.nrm, piece.uv0, piece.uv1, piece.tris)


def fm1_bead(parts):
    """The inner radius of FM1's tire (material 8) at its LOD 1: where its rim meets it."""
    radii = [math.hypot(part.verts[i][0][1], part.verts[i][0][2]) for part in parts
             for lod, mat, idx in part.subparts if lod == 1 and mat == FM1_TIRE for i in idx if i < len(part.verts)]
    return min(radii) if radii else None


def wheel_pieces(parts, section, scale):
    top = max((s[0] for p in parts for s in p.subparts), default=1)
    out = []
    for part in parts:
        pos = [tuple(c * scale for c in v[0]) for v in part.verts]  # both games: the outer face at -x
        nrm = [v[1] for v in part.verts]
        uv = [v[2] for v in part.verts]
        for fh1_lod in range(6):
            src = 1 if fh1_lod == 0 else min(fh1_lod, top)
            for lod, mat, idx in part.subparts:
                if lod == src and mat in WHEEL_MATERIALS:
                    tris = fm1_cars.strip_triangles(idx)
                    if tris:
                        out.append(fm1_cars.cb.Piece(section.name, WHEEL_MATERIALS[mat], fh1_lod, pos, nrm, uv, uv, tris))
    bead = fm1_bead(parts)
    tires = [p for p in fm1_cars.cb.section_pieces(section, {"tire"}) if p.lod > 0]
    return out + [fit_tire(p, bead * scale) if bead else p for p in tires]


def wheel_files(fh1, fm1_dir, car_name, donor_wheel, media):
    """{mod path: bytes}: the donor's wheel archive as media/wheels/<media>/, FM1's wheel in it."""
    import fm4_cars
    cb = fm1_cars.cb
    path = fm4_cars.find_ci(fh1.media, "wheels", donor_wheel + ".zip")
    if not path:
        raise Fm1ImportError("no media/wheels/%s.zip" % donor_wheel)
    z = fm4_cars.ZIPS.get(path)
    members = [(i.filename, fm4_cars.Member(z, path, i)) for i in z.infolist() if not i.filename.endswith("/")]
    _, parts, tex, vol = fm1_cars.load_wheel(fm1_dir, car_name)
    out = {}
    base = "media/wheels/%s/" % media
    for name, member in members:
        low = name.lower()
        rel = rename(name, donor_wheel, media)
        if low.endswith("_build_report.html"):
            continue
        if low.endswith(".carbin") and not low.startswith("stripped_"):
            donor = fc.parse(member.read())
            sec = donor.sections[0]
            radius = max(abs(p[1]) for p in fc.world(sec, sec.lod))
            fm1_radius = max(abs(v[0][1]) for p in parts for v in p.verts)
            pieces = wheel_pieces(parts, sec, radius / fm1_radius)
            new, _ = cb.build(donor, pieces, "auto", cb.SHModel([donor]), seed=7)
            out[base + rel] = fc.build(new)
        elif low.endswith(".xds") and tex is not None:
            w, h, (levels,) = fm1_cars.dxt5_layers(tex)
            if len(levels) < 2:
                levels = fm1_cars.fh1_xds.mips_from(levels[0], w, h)
            m = re.match(r"wheel(\d)\.xds$", low)
            if m and vol is not None:  # the spin blur: FM1's blur slices in turn
                vw, vh, slices = fm1_cars.dxt5_layers(vol)
                s = slices[int(m.group(1)) % len(slices)][0]
                out[base + rel] = fm1_cars.fh1_xds.write_dxt5(fm1_cars.fh1_xds.mips_from(s, vw, vh), vw, vh)
            else:
                out[base + rel] = fm1_cars.fh1_xds.write_dxt5(levels, w, h)
        else:
            data = member.read()
            if low.endswith((".xml", ".txt")):
                data = re.sub(re.escape(donor_wheel).encode(), media.encode(), data, flags=re.I)
            out[base + rel] = data
    return out


def wheel_rows(db, rows, donor_wheel_id, new_id, media):
    """The new wheel's List_Wheels row (the donor's, renamed) and the car's StockWheelID. The
    wheel takes the car's id when List_Wheels has it free, else its next free id."""
    cols = columns(db, "List_Wheels")
    row = dict(zip(cols, db.execute("select * from List_Wheels where ID = ?", (donor_wheel_id,)).fetchone()))
    wid = new_id
    if db.execute("select 1 from List_Wheels where ID = ?", (wid,)).fetchone():
        wid = db.execute("select max(ID) from List_Wheels").fetchone()[0] + 1
    donor_media = row["MediaName"]
    row.update(ID=wid, MediaName=media)
    if str(row.get("IconPath") or "").lower() == str(donor_media).lower():
        row["IconPath"] = media
    rows["List_Wheels"] = [row]
    rows["Data_Car"][0]["StockWheelID"] = wid
    return donor_media, wid


# ============================================================================= names

def string_tables(tables, texts, hi):
    """Each language's Data_Car table with the car's names added under one key per name, free in
    every language. tables: {lang: StringTable}; texts: [(IDS_ name, text)]. Returns (references,
    {lang: table bytes})."""
    used = set().union(*(set(t.entries) for t in tables.values())) if tables else set()
    keys, k = [], 0x8000
    for _ in texts:
        while k in used or k == 0xFFFF:
            k = (k + 1) & 0xFFFF
        keys.append(k)
        used.add(k)
    out = {}
    for lang, t in tables.items():
        for (ids, text), key in zip(texts, keys):
            if t.add(ids, text, 0, key) != key:
                raise Fm1ImportError("the %s table already has %s under another key" % (lang, ids))
        out[lang] = t.encode()
    return ["_&%d" % (hi << 16 | key) for key in keys], out


# ============================================================================= the mod

def donor_members(fh1, donor):
    import fm4_cars
    path = fm4_cars.find_ci(fh1.media, "cars", donor + ".zip")
    if not path:
        raise Fm1ImportError("no media/cars/%s.zip" % donor)
    z = fm4_cars.ZIPS.get(path)
    return [(i.filename, fm4_cars.Member(z, path, i)) for i in z.infolist() if not i.filename.endswith("/")]


def rename(name, donor, media):
    return re.sub(re.escape(donor), media, name, flags=re.I)


def car_files(fh1, donor, media, models, textures):
    """{mod path: bytes}: the donor's car files renamed, with the FM1 car's models (and their
    stripped_ copies) and textures in them. A build report of the donor's is left out."""
    out = {}
    base = "media/cars/%s/" % media
    for name, member in donor_members(fh1, donor):
        low = name.lower()
        if low.endswith("_build_report.html"):
            continue
        rel = rename(name, donor, media)
        if name in models:
            out[base + rel] = fc.build(models[name])
        elif low.startswith("stripped_") and name[len("stripped_"):].lower() in {m.lower() for m in models}:
            src = next(m for m in models if m.lower() == name[len("stripped_"):].lower())
            out[base + rel.lower()] = fc.build(models[src], stripped=True)
        elif name in textures:
            out[base + rel] = textures[name]
        else:
            data = member.read()
            if low.endswith((".xml", ".txt")):
                data = re.sub(re.escape(donor).encode(), media.encode(), data, flags=re.I)
            out[base + rel] = data
    return out


def audio_files(fh1, donor, media):
    """The donor's sound tuning (CarModelTuning, EngineTuning), renamed for the new car."""
    import fm4_cars
    out = {}
    for archive, folder, suffix in AUDIO:
        name = donor + suffix
        data = fm4_cars.zip_member(fh1.update, folder[len("media/"):] + name.lower()) if fh1.update else None
        if data is None:
            path = fm4_cars.find_ci(fh1.media, *archive.split("/"))
            data = fm4_cars.zip_member(path, name) if path else None
        if data is not None:
            out[folder + media + suffix] = re.sub(re.escape(donor).encode(), media.encode(), data, flags=re.I)
    return out


def harmonic_text(fh1, name):
    """An FH1 HarmonicTuning file's text (the title update's first), or None."""
    import fm4_cars
    data = fm4_cars.zip_member(fh1.update, "audio/cars/engines/harmonictuning/" + name.lower()) if fh1.update else None
    if data is None:
        path = fm4_cars.find_ci(fh1.media, "audio", "cars", "Engines", "HarmonicTuning.zip")
        data = fm4_cars.zip_member(path, name) if path else None
    return data.decode("utf-8", "replace") if data is not None else None


def fm1_sound_files(fh1, cab, media, files):
    """FM1's own engine sound in place of the FH1 one files holds for media (its EngineTuning,
    replaced): ({mod path: bytes} and {BANKS + bank name: bytes}, None), or ({}, why not)."""
    import fm1_sound
    et_path = AUDIO[1][1] + media + AUDIO[1][2]
    if et_path not in files:
        return {}, "no EngineTuning to start from"
    try:
        sound = fm1_sound.car_sound(cab)
        if sound is None:
            return {}, "FM1's cab has no engine sound"
        et, tunings, banks = fm1_sound.fh1_files(sound, media, files[et_path].decode("utf-8", "replace"),
                                                 lambda n: harmonic_text(fh1, n))
    except fm1_sound.Fm1SoundError as e:
        return {}, str(e)
    out = {et_path: et.encode("utf-8")}
    for name, text in tunings.items():
        out["media/audio/cars/engines/harmonictuning/" + name.lower()] = text.encode("utf-8")
    for name, data in banks.items():
        out[BANKS + name] = data
    return out, None


def write_package(package, files):
    """FM1's sound banks (files' BANKS entries) as a DLC package of their own. The game opens a
    bank as a file at game:\\Media\\audio\\cars\\Engines\\Soundbanks\\LOD1\\<bank>, never from a
    zip or the mod (#776); FH1's own DLC packs bring theirs as a folder their Media/DLCZips/
    zipmount.xml mounts there (LOD1_pri_1650), and so does this one (FM1Banks_pri_60). package:
    the package folder, .../4D5309C9/00000002/<PACKAGE_NAME> (its header goes into
    .../4D5309C9/Headers/00000002). The cars and their tuning stay in the mod.
    -> the bank names written."""
    import install_dlc
    banks = {p[len(BANKS):]: d for p, d in files.items() if p.startswith(BANKS)}
    if not banks:
        return []
    media = os.path.join(package, "Media")
    folder = os.path.join(media, "DLCZips", BANKS_FOLDER)
    os.makedirs(folder, exist_ok=True)
    for name, data in banks.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
    with open(os.path.join(media, "DLCZips", "zipmount.xml"), "w", encoding="ascii", newline="\r\n") as f:
        f.write('<?xml version="1.0" ?>\n<zipmount>\n   <zip Name="%s" Mount="game:\\Media\\audio\\cars\\Engines\\'
                'Soundbanks\\LOD1\\" AltRootPath="game:\\Media\\audio\\cars\\Engines\\Soundbanks\\LOD1\\" '
                'ShouldCache="0" />\n</zipmount>\n' % BANKS_FOLDER)
    with open(os.path.join(media, "%d.puboffer" % PACKAGE_OFFER), "wb") as f:
        f.write(b" \r\n")
    name = os.path.basename(os.path.normpath(package))
    headers = os.path.join(os.path.dirname(os.path.dirname(os.path.normpath(package))), "Headers", "00000002")
    os.makedirs(headers, exist_ok=True)
    with open(os.path.join(headers, name + ".header"), "wb") as f:
        f.write(install_dlc.header_bytes(name, "Forza Motorsport (2005) car sounds", install_dlc.TITLE_ID))
    return sorted(banks)


def thumbnail(fh1, donor_id, new, draw=None):
    """The car list's picture: FM1's model drawn (draw: a function returning fm1_thumbnail's RGBA
    bytes) into the donor's thumbnail as the template; the donor's picture if it can't be."""
    import fm4_cars
    path = fm4_cars.find_ci(fh1.media, "ui", "Textures.zip")
    data = fm4_cars.zip_member(path, "Thumbnails/Thumbnail_%d.xds" % donor_id) if path else None
    if data is None:
        return {}
    if draw is not None:
        import fm1_thumbnail
        try:
            data = fm1_thumbnail.to_xds(draw(), data)
        except (ValueError, fm1_cars.Fm1Error) as e:
            print("thumbnail: the donor's picture kept (%s)" % e)
    return {"media/ui/textures/thumbnails/thumbnail_%d.xds" % new: data}


def build(fm1_dir, fh1_dir, car_name, donor, update=None, base_mod=None, year=None, sound=False):
    """({mod path: bytes}, report) for an FM1 car as a car of its own, on donor's files and rows.
    base_mod: a mod folder (an FM4 cars mod) whose gamedb.slt and data_car.str tables to add the
    car to, instead of the game's (only one mod's database is used: this one then goes after it).
    sound: FM1's own engine sound (fm1_sound) on the matched FH1 sound's tuning; its banks come
    back as BANKS entries, for write_banks."""
    import fm4_cars
    cab, car =fm1_cars.load_car(fm1_dir, car_name)
    info = fm1_info(fm1_dir, cab, car_name)
    fh1 = fm4_cars.FH1(fh1_dir, update)
    game_db = fh1.db()
    game_db.close()
    source = os.path.join(base_mod, "media", "db", "gamedb.slt") if base_mod else None
    if not (source and os.path.isfile(source)):
        source = fh1.db_file
    db = sqlite3.connect("file:%s?mode=ro" % source, uri=True)
    try:
        drow = db.execute("select Id, DisplayName from Data_Car where MediaName = ? collate nocase", (donor,)).fetchone()
        if not drow:
            raise Fm1ImportError("no car %s in FH1's database" % donor)
        donor_id = drow[0]
        new = NEW_ID + info["ordinal"]
        if db.execute("select 1 from Data_Car where Id = ?", (new,)).fetchone():
            raise Fm1ImportError("FH1 already has a car %d" % new)
        _, code = fh1_make(db, info["make"])
        media = media_name(code, info, lambda m: bool(db.execute(
            "select 1 from Data_Car where MediaName = ? collate nocase", (m,)).fetchone()))
        if db.execute("select 1 from Data_Car where MediaName = ? collate nocase", (media,)).fetchone():
            raise Fm1ImportError("FH1 already has a car %s" % media)
        hi = int(drow[1][2:]) >> 16
        display, short = names(info)
        tables = {}
        for lang in sorted(fh1.languages()):
            if lang in ("dev", "loc"):
                continue
            path = os.path.join(base_mod, "media", "stringtables", lang, "data_car.str") if base_mod else None
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    tables[lang] = fm4_cars.StringTable(f.read())
            else:
                tables[lang] = fh1.strings_raw("Data_Car", lang)[1]
        refs, encoded = string_tables(tables, [("IDS_DisplayName_%d" % new, display),
                                               ("IDS_ModelShort_%d" % new, short)], hi)
        files_, wheels, xds = fm1_cars._fh1_donor(fh1_dir, update, donor)
        models = fm1_cars.build_models(car, files_, wheels, info["year"] if year is None else year)
        sw = next((r.get("steering_wheel_pivot") for n, (_, r) in models.items() if not re.search(r"(?i)_(lod0|cockpit)", n)), None)
        dy, dz = fm1_cars.placement(car, wheels)
        found = fm1_cars.lamps(car, fm1_cars.stock_parts(car), dy, dz)
        rows, donor_media = car_rows(db, donor_id, new, media, info, refs, sw, found, fm1_physics(fm1_dir, car_name))
        donor_wheel, wheel_id = wheel_rows(db, rows, rows["Data_Car"][0]["StockWheelID"], new, media)
        spec = ENGINE_SPECS.get(car_name.lower()) or donor_spec(db, donor_id, donor_media)
        eng = fm1_engine(fm1_dir, car_name)
        if not spec.get("sound"):  # a sound from an FH1 engine of the same kind, never the donor's by default
            kind = spec.get("aspiration") or eng["aspiration"]
            have = lambda m: bool(fm4_cars.zip_member(fm4_cars.find_ci(fh1.media, "audio", "cars", "Engines", "EngineTuning.zip"),
                                                      m + "_ET.xml"))  # noqa: E731
            spec = dict(spec, sound=pick_sound(db, spec["config"], spec["cylinders"], ASPIRATIONS[kind][0],
                                               fh1_make(db, info["make"])[0], info["year"], have) or donor_media)
        engine_id, drivetrain_id = engine_rows(db, rows, eng, spec, new, media)
        measured = apply_ratings(db, rows["Data_Car"][0], fm1_figures(fm1_dir, info["ordinal"]),
                                 RATINGS.get(car_name.lower()))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            path = os.path.join(td, "gamedb.slt")
            fm4_cars.write_full_db(source, db, rows, path)
            with open(path, "rb") as f:
                full = f.read()
    finally:
        db.close()
        fh1.close()
    files = {GAMEDB: full}
    files.update(car_files(fh1, donor_media, media, {n: m for n, (m, _) in models.items()},
                           fm1_cars.textures(cab, fm1_cars.cab_name(car, cab), xds)))
    files.update(wheel_files(fh1, fm1_dir, car_name, donor_wheel, media))
    files.update(audio_files(fh1, spec.get("sound") or donor_media, media))
    own_sound, no_sound = fm1_sound_files(fh1, cab, media, files) if sound else ({}, "not asked for")
    files.update(own_sound)
    import fm1_thumbnail
    paint = info["colours"][0][:3] if info["colours"] else (128, 128, 128)
    files.update(thumbnail(fh1, donor_id, new, lambda: fm1_thumbnail.car_thumbnail(fm1_dir, car_name, paint)))
    for lang, data in encoded.items():
        files["media/stringtables/%s/data_car.str" % lang] = data
    report = {"id": new, "media": media, "names": (display, short), "make": info["make"], "year": info["year"],
              "donor": donor_media, "wheel": (wheel_id, donor_wheel), "engine": (engine_id, drivetrain_id),
              "sound": ("FM1's own (%d banks), on %s's tuning" % (len([p for p in own_sound if p.startswith(BANKS)]),
                                                                   spec.get("sound") or donor_media) if own_sound
                        else "%s (FM1's: %s)" % (spec.get("sound") or donor_media, no_sound)),
              "engine spec": "the donor's engine (ENGINE_SPECS has no %s)" % car_name.lower() if spec.get("from_donor")
                             else "ENGINE_SPECS",
              "ratings": "read from the game" if measured else "the donor's: RATINGS has no %s (read them in the "
                         "game's Custom Upgrade screen and add them)" % car_name.lower(),
              "rows": {t: len(r) for t, r in rows.items()}, "files": len(files),
              "models": {n: {k: v for k, v in r.items() if k in ("filled", "emptied", "kept", "lights")}
                         for n, (_, r) in models.items()}}
    return files, report


def write_mod(out, files, report):
    """The mod's files (not the banks: write_package)."""
    for path, data in files.items():
        if path.startswith(BANKS):
            continue
        full = os.path.join(out, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
    with open(os.path.join(out, "mod.toml"), "w", encoding="utf-8") as f:
        f.write(MOD_TOML % ("FM1 %s" % report["names"][1].replace(" (FM1)", ""),
                            "Forza Motorsport's %s as a car of its own, id %d (#930)" % (report["names"][0], report["id"])))


def build_many(fm1_dir, fh1_dir, pairs, update=None, base_mod=None, log=print, sound=False):
    """({mod path: bytes}, [reports], [(car, error)]): several FM1 cars in one mod, each car's
    rows added to the database (and names to the string tables) the previous car's import
    wrote; a car that fails is left out and the next goes on. pairs: [(FM1 car, FH1 donor)]."""
    files, reports, failed = {}, [], []
    base = base_mod
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        for k, (car, donor) in enumerate(pairs):
            try:
                f, report = build(fm1_dir, fh1_dir, car, donor, update, base, sound=sound)
            except (Fm1ImportError, fm1_cars.Fm1Error, ValueError, KeyError, sqlite3.Error) as e:
                failed.append((car, "%s: %s" % (type(e).__name__, e)))
                log("%s: failed (%s)" % (car, e))
                continue
            files.update(f)
            reports.append(report)
            base = os.path.join(td, "step%d" % k)
            write_mod(base, {p: d for p, d in f.items() if p == GAMEDB or p.endswith("/data_car.str")}, report)
            log("%s: id %d, %s" % (car, report["id"], report["media"]))
    return files, reports, failed


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("fm1", help="FM1's disc (a folder with media/cabs)")
    ap.add_argument("fh1", help="the FH1 game folder (with media)")
    ap.add_argument("car", help="FM1's name for the car (bordermr2), or several FM1=DONOR pairs, "
                    "comma-separated (bordermr2=MAZ_RX7_97,HondaS2000=HON_S2000_03): one mod for them all")
    ap.add_argument("donor", nargs="?", help="the FH1 car whose files and rows it starts from (MAZ_RX7_97)")
    ap.add_argument("out", help="the mod folder to write")
    ap.add_argument("--update", help="FH1's title update folder (with media.zip)")
    ap.add_argument("--base-mod", help="a mod folder (an FM4 cars mod) whose gamedb.slt and data_car.str "
                    "tables to add the car to, instead of the game's; load this mod after it")
    ap.add_argument("--package", help="the DLC package folder to write for FM1's own engine sound banks "
                    "(.../4D5309C9/00000002/%s; the game reads banks only from the disc or a package); "
                    "without it the cars sound like the FH1 engine matched to theirs" % PACKAGE_NAME)
    a = ap.parse_args(argv)
    sound = bool(a.package)
    if "=" in a.car:
        pairs = [tuple(p.split("=", 1)) for p in a.car.split(",") if p]
        files, reports, failed = build_many(a.fm1, a.fh1, pairs, a.update, a.base_mod, sound=sound)
        if not reports:
            print("error: no car imported", file=sys.stderr)
            return 1
        write_mod(a.out, files, reports[-1])
        if a.package:
            print("banks:", ", ".join(write_package(a.package, files)) or "none")
        with open(os.path.join(a.out, "mod.toml"), "w", encoding="utf-8") as f:
            f.write(MOD_TOML % ("FM1 cars", "Forza Motorsport cars as cars of their own (#930): "
                                + ", ".join(r["names"][1].replace(" (FM1)", "") for r in reports)))
        for car, err in failed:
            print("failed: %s: %s" % (car, err))
        return 0 if not failed else 2
    if not a.donor:
        ap.error("a donor is needed for one car")
    try:
        files, report = build(a.fm1, a.fh1, a.car, a.donor, a.update, a.base_mod, sound=sound)
    except (Fm1ImportError, fm1_cars.Fm1Error) as e:
        print("error:", e, file=sys.stderr)
        return 1
    write_mod(a.out, files, report)
    if a.package:
        report["banks"] = write_package(a.package, files)
    for k, v in report.items():
        print("%s: %s" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
