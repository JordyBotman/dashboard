from flask import Flask, render_template_string, url_for, abort, request, redirect, jsonify
from pathlib import Path
from decimal import Decimal, InvalidOperation
from threading import RLock
from uuid import uuid4
import json
import os
import tempfile
import shutil
from datetime import date, timedelta

app = Flask(__name__)
app.json.ensure_ascii = False
DATA_FILE = Path(__file__).resolve().with_name("wouters_data.json")
LOCK = RLock()
FASEN = ["beworteling", "oppotfase", "uitzetfase", "afleverfase"]
VERKOOP = ["P09 Viola F1", "P10,5 Seasons Best", "P12 Festival Colours",
           "P13 Wouters & Wouters", "P17 Festival Colours", "P19 Bolchrysanten"]
INKOOP = ["P09868v POT 9CM 8° Lv", "P105590 POT 10.5CM 5° H",
          "P125C100 POT 12CM 5° H", "Pot 13 cm 5° YB",
          "P175128v POT 17CM 5° Lv", "P195149 POT 19CM 5° L"]


POTINHOUD_LITERS = dict(zip(INKOOP, [0.5, 0.6, 0.8, 1.0, 2.0, 3.0]))


def standaard_m2_prijzen():
    return [{"week": week, "prijs_per_m2": 10 if week <= 52 else None}
            for week in range(1, 54)]


def standaard_toegevoegde_waarde():
    return [{"id": "waarde-" + str(i), "naam": naam, "prijs_per_stuk": prijs}
            for i, (naam, prijs) in enumerate([
                ("Bambini met etiket", 0.01), ("Draagbeugel smal", 0.02),
                ("Draagbeugel breed", 0.03), ("Blanco Hoes", 0.04)], 1)]


def standaard_substraten():
    return [{"id": "substraat-" + str(i), "naam": naam, "prijs_per_liter": prijs}
            for i, (naam, prijs) in enumerate([
                ("Violengrond", 0.60), ("Perkgoedgrond", 0.70),
                ("Bolchrysantengrond", 0.80), ("Kerststerrengrond", 0.90)], 1)]


def standaard_schemas():
    return [{"id": "schema-" + str(i), "naam": naam,
             "fasen": {fase: {"weken": i + 4, "planten_per_m2": i * 100}
                       for fase in FASEN}}
            for i, naam in enumerate(VERKOOP, 1)]


def begindata():
    return {
        "verkoopartikelen": [{"id": str(i), "naam": n, "substraat_id": None} for i, n in enumerate(VERKOOP, 1)],
        "opkweekverpakkingen": [{"id": str(i), "naam": n, "prijs_per_1000": prijs, "inhoud_liter": POTINHOUD_LITERS[n]}
                            for i, (n, prijs) in enumerate(zip(INKOOP, [90, 100, 120, 130, 170, 190]), 1)],
        "productieschemas": standaard_schemas(),
        "schemas_startversie": 1,
        "potinhoud_startversie": 1,
        "substraatprijs_startversie": 1,
        "substraten": standaard_substraten(),
        "toegevoegde-waarde": standaard_toegevoegde_waarde(),
        "prijs-per-m2": standaard_m2_prijzen(),
        "klant-offertes": [],
        "vertaling-verkoop-inkoop": [{"verkoop_id": str(i), "inkoop_id": str(i)} for i in range(1, 7)],
    }


def bewaren(data):
    # Schrijf eerst een tijdelijk bestand om onvolledige opslag te voorkomen.
    fd, tijdelijk = tempfile.mkstemp(dir=DATA_FILE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tijdelijk, DATA_FILE)
    finally:
        if os.path.exists(tijdelijk):
            os.unlink(tijdelijk)


def lezen():
    with LOCK:
        if not DATA_FILE.exists():
            bewaren(begindata())
        # Een beschadigd bestand wordt nooit stilzwijgend overschreven.
        with DATA_FILE.open(encoding="utf-8-sig") as f:
            data = json.load(f)
        # Vul ook een bestaand gegevensbestand eenmalig aan.
        # Bestaande schema's met dezelfde naam behouden hun eigen waarden en ID.
        if data.get("schemas_startversie", 0) < 1:
            namen = {s["naam"].casefold() for s in data["productieschemas"]}
            ids = {s["id"] for s in data["productieschemas"]}
            for schema in standaard_schemas():
                if schema["naam"].casefold() not in namen:
                    if schema["id"] in ids:
                        schema["id"] = uuid4().hex
                    data["productieschemas"].append(schema)
            data["schemas_startversie"] = 1
            bewaren(data)
        if "substraten" not in data:
            data["substraten"] = standaard_substraten()
            bewaren(data)
        if "toegevoegde-waarde" not in data:
            data["toegevoegde-waarde"] = standaard_toegevoegde_waarde()
            bewaren(data)
        if "prijs-per-m2" not in data:
            data["prijs-per-m2"] = standaard_m2_prijzen()
            bewaren(data)
        gewijzigd = False
        if "klant-offertes" not in data:
            data["klant-offertes"] = []
            gewijzigd = True
        # Werk de vier doorgegeven substraatprijzen eenmalig bij.
        if data.get("substraatprijs_startversie", 0) < 1:
            prijzen = {s["naam"].casefold(): s["prijs_per_liter"] for s in standaard_substraten()}
            for substraat in data["substraten"]:
                naam = substraat["naam"].strip().casefold()
                if naam in prijzen:
                    substraat["prijs_per_liter"] = prijzen[naam]
            data["substraatprijs_startversie"] = 1
            gewijzigd = True
        if "opkweekverpakkingen" not in data and "inkoopartikelen" in data:
            data["opkweekverpakkingen"] = data.pop("inkoopartikelen")
            gewijzigd = True
        for artikel in data["opkweekverpakkingen"]:
            if "inhoud_liter" not in artikel:
                artikel["inhoud_liter"] = None
                gewijzigd = True
        # Pas de zes doorgegeven potinhouden eenmalig toe, ook in oude bestanden.
        # Latere handmatige aanpassingen blijven vervolgens behouden.
        if data.get("potinhoud_startversie", 0) < 1:
            inhoud_per_naam = {naam.casefold(): liters for naam, liters in POTINHOUD_LITERS.items()}
            for artikel in data["opkweekverpakkingen"]:
                naam = artikel["naam"].strip().casefold()
                if naam in inhoud_per_naam:
                    artikel["inhoud_liter"] = inhoud_per_naam[naam]
            data["potinhoud_startversie"] = 1
            gewijzigd = True
        for artikel in data["verkoopartikelen"]:
            if "substraat_id" not in artikel:
                artikel["substraat_id"] = None
                gewijzigd = True
        if gewijzigd:
            bewaren(data)
        return data


def getal(waarde, label, geheel=False):
    try:
        d = Decimal(str(waarde).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Vul een geldig getal in voor {label}.")
    if not d.is_finite() or d < 0 or d > 1000000000:
        raise ValueError(f"{label}: gebruik een getal tussen 0 en 1 miljard.")
    if geheel and d != d.to_integral_value():
        raise ValueError(f"{label}: gebruik een heel aantal weken.")
    return int(d) if geheel else float(d)


def substraat_invoer(data):
    substraat_id = request.form.get("substraat_id", "").strip() or None
    if substraat_id is not None and not any(s["id"] == substraat_id for s in data["substraten"]):
        raise ValueError("Kies een bestaand substraat uit de lijst.")
    return substraat_id


def inhoud_invoer():
    waarde = request.form.get("inhoud_liter", "").strip()
    return getal(waarde, "inhoud in liters grond") if waarde else None


def naam_invoer():
    naam = request.form.get("naam", "").strip()
    if not naam or len(naam) > 200:
        raise ValueError("Vul een naam in van maximaal 200 tekens.")
    return naam


def verrijk(data, slug):
    if slug == "verkoopartikelen":
        substraten = {s["id"]: s for s in data["substraten"]}
        return [dict(a, substraat_naam=substraten.get(a.get("substraat_id"), {}).get("naam"))
                for a in data[slug]]
    if slug == "opkweekverpakkingen":
        return [dict(a, prijs_per_stuk=float(Decimal(str(a["prijs_per_1000"])) / 1000))
                for a in data[slug]]
    if slug == "vertaling-verkoop-inkoop":
        verkoop = {a["id"]: a["naam"] for a in data["verkoopartikelen"]}
        inkoop = {a["id"]: a for a in verrijk(data, "opkweekverpakkingen")}
        return [dict(k, verkoopartikel=verkoop[k["verkoop_id"]],
                     inkoopartikel=inkoop[k["inkoop_id"]]["naam"],
                     prijs_per_stuk=inkoop[k["inkoop_id"]]["prijs_per_stuk"]) for k in data[slug]]
    return data[slug]


@app.template_filter("euro")
def euro(waarde, decimalen=2):
    return "€ " + f"{waarde:,.{decimalen}f}".replace(",", "X").replace(".", ",").replace("X", ".")

ONDERDELEN = {
    "verkoopartikelen": {
        "naam": "Verkoopartikelen", "icoon": "✿", "nummer": "01",
        "tekst": "Jouw assortiment, overzichtelijk bij elkaar.", "kleur": "groen",
    },
    "opkweekverpakkingen": {
        "naam": "Opkweekverpakkingen", "icoon": "▦", "nummer": "02",
        "tekst": "Alles wat je nodig hebt voor een sterke start.", "kleur": "geel",
    },
    "productieschemas": {
        "naam": "Productieschema's", "icoon": "▤", "nummer": "03",
        "tekst": "Van planning naar productie. Houd grip op je teelt.", "kleur": "roze",
    },
    "vertaling-verkoop-inkoop": {
        "naam": "Vertaling verkoop-inkoop", "icoon": "⇄", "nummer": "04",
        "tekst": "De verbinding tussen verkoop en inkoop.", "kleur": "blauw",
    },
    "substraten": {
        "naam": "Substraten", "icoon": "❧", "nummer": "05",
        "tekst": "Een goede basis voor je teelt. Bekijk en beheer de prijs per liter.", "kleur": "groen",
    },
    "toegevoegde-waarde": {
        "naam": "Toegevoegde waarde", "icoon": "✧", "nummer": "06",
        "tekst": "Etiketten, draagbeugels en hoezen. Beheer de prijs per stuk.", "kleur": "geel",
    },
    "prijs-per-m2": {
        "naam": "Prijs per m2", "icoon": "▧", "nummer": "07",
        "tekst": "Beheer de kostprijs per m² voor iedere ISO-week.", "kleur": "blauw",
    },
    "maak-kostprijsberekening": {
        "naam": "Maak kostprijsberekening", "icoon": "€", "nummer": "08",
        "tekst": "Bereken de productkosten voor jouw klant en eindklant.", "kleur": "groen",
    },
}

PAGINA = """
<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ onderdeel.naam if onderdeel else 'Start' }} | Wouters</title>
  <style>
    :root { --groen:#244c36; --inkt:#23382c; --licht:#f6f7f2; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--licht); color:var(--inkt);
           font-family:Arial,Helvetica,sans-serif; }
    header { max-width:1180px; margin:auto; padding:28px 32px;
             display:flex; justify-content:space-between; align-items:center; gap:20px; }
    .logo { text-decoration:none; color:var(--groen); font-size:25px; font-weight:800;
            letter-spacing:-1px; display:flex; align-items:center; gap:10px; }
    .merkteken { display:grid; place-items:center; width:38px; height:38px;
                 background:var(--groen); color:#d7ed86; border-radius:12px; }
    .portaal { font-size:12px; font-weight:bold; letter-spacing:2px; text-transform:uppercase; }
    main { max-width:1180px; margin:auto; padding:18px 32px 48px; }
    .hero { position:relative; overflow:hidden; border-radius:28px;
            background:var(--groen); color:white; padding:58px; min-height:300px; }
    .hero::before,.hero::after { content:''; position:absolute; background:#d2e681;
      border-radius:100% 0 100% 0; transform:rotate(-20deg); pointer-events:none; }
    .hero::before { width:260px; height:390px; right:-30px; top:-90px; opacity:.13; }
    .hero::after { width:160px; height:280px; right:120px; top:140px; opacity:.08; }
    .hero>* { position:relative; z-index:1; }
    .label { margin:0 0 18px; text-transform:uppercase; letter-spacing:2px;
             color:#d4e994; font-size:11px; font-weight:bold; }
    h1 { font-size:clamp(32px,5vw,54px); letter-spacing:-2px; line-height:1.08;
         margin:0 0 20px; max-width:750px; }
    .intro { color:#dbe7df; line-height:1.7; max-width:520px; margin:0; font-size:16px; }
    .sectiekop { display:flex; justify-content:space-between; align-items:center;
                  margin:34px 0 18px; gap:16px; }
    h2 { font-size:20px; margin:0; letter-spacing:-.5px; }
    .hint { color:#758174; font-size:13px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; }
    .kaart { text-decoration:none; color:var(--inkt); background:white;
             border:1px solid #e1e7dc; border-radius:22px; padding:28px;
             transition:transform .2s,box-shadow .2s,border-color .2s; }
    .kaart:hover { transform:translateY(-4px); border-color:#9ab68c;
                   box-shadow:0 14px 32px #254b3610; }
    a:focus-visible { outline:3px solid #93b638; outline-offset:5px; }
    .kaartboven { display:flex; justify-content:space-between; align-items:center; }
    .icoon { display:grid; place-items:center; width:54px; height:54px;
             border-radius:16px; font-size:30px; line-height:1; }
    .groen { background:#e9f2d7; color:#52752b; }
    .geel { background:#fff2cb; color:#96741b; }
    .roze { background:#f9e7e3; color:#a76855; }
    .blauw { background:#e5eff8; color:#4a7593; }
    .nummer { font-size:12px; color:#899287; }
    h3 { font-size:23px; letter-spacing:-.7px; margin:25px 0 10px; }
    .kaart p { margin:0; color:#748071; font-size:14px; line-height:1.6; }
    .open { display:flex; justify-content:space-between; align-items:center;
            margin-top:26px; font-size:13px; font-weight:bold; color:var(--groen); }
    .pijl { background:#f0f4e9; border-radius:50%; display:grid;
            place-items:center; width:32px; height:32px; font-size:19px; }
    .inhoud { background:white; border:1px solid #e1e7dc; padding:32px;
              border-radius:22px; margin-top:24px; line-height:1.7; }
    .terug { display:inline-block; margin-top:16px; padding:13px 20px;
             background:var(--groen); color:white; border-radius:10px; text-decoration:none; }
    footer { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;
             padding-top:30px; color:#7b8778; font-size:12px; }
    @media(max-width:640px) {
      header { padding:22px 20px; } main { padding:10px 20px 30px; }
      .portaal { font-size:10px; letter-spacing:1px; }
      .hero { padding:34px 26px; min-height:auto; } h1 { letter-spacing:-1px; }
      .grid { grid-template-columns:1fr; } .hint { display:none; }
      .kaart { padding:24px; } h3 { font-size:22px; }
    }
    @media(prefers-reduced-motion:reduce) { .kaart { transition:none; } }
    form { margin:22px 0; } label { display:block; font-weight:bold; font-size:14px; }
    input,select { width:100%; padding:12px; border:1px solid #cdd8c9; border-radius:9px;
      margin:6px 0 16px; font:inherit; background:white; color:var(--inkt); }
    button { padding:12px 20px; background:var(--groen); color:white; border:0;
      border-radius:9px; font:inherit; cursor:pointer; }
    .substraatformulier { display:flex; align-items:center; gap:10px; margin:0; }
    .substraatformulier select { margin:0; min-width:180px; }
    .velden { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:16px; }
    .tabel { overflow-x:auto; } table { width:100%; border-collapse:collapse; font-size:14px; }
    th,td { padding:14px 12px; border-bottom:1px solid #e5ebdf; text-align:left; }
    th { background:#f1f5eb; white-space:nowrap; } .melding { padding:15px; border-radius:10px;
      background:#e9f2d7; margin-top:20px; } .fout { background:#ffe4de; }
    .prijsformulier { display:flex; gap:8px; align-items:center; margin:0; }
    .prijsformulier input { width:125px; margin:0; } .prijsformulier button { padding:10px; }
    .api { display:inline-block; margin:16px; color:var(--groen); }
    body.schema-pagina main,body.schema-pagina header { max-width:1700px; }
    .schematabel { min-width:1250px; }
    .schematabel th { white-space:normal; min-width:100px; font-size:12px; }
    .schematabel th:first-child { min-width:235px; }
    .schematabel td { padding:10px 7px; }
    .schematabel input { margin:0; min-width:75px; padding:10px 7px; }
    .schematabel .schemanaam { min-width:220px; }
    .schematabel tbody tr:hover { background:#f8faf4; }
    .schematabel .nieuw { background:#f0f5e7; }
    input:focus-visible,button:focus-visible { outline:3px solid #93b638; outline-offset:2px; }
    .weekgrid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:20px 0; }
    .weekkaart { background:#f6f8f1; border:1px solid #e1e7dc; border-radius:12px; padding:14px; }
    .weekkaart input { margin:8px 0 0; }
    .weekkaart small { color:#748071; font-weight:normal; }
    @media(max-width:1000px) { .weekgrid { grid-template-columns:repeat(3,minmax(0,1fr)); } }
    @media(max-width:700px) { .weekgrid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media(max-width:400px) { .weekgrid { grid-template-columns:1fr; } }
    .calculatievelden { display:grid; grid-template-columns:1fr 1fr 1.4fr .7fr .7fr .7fr 1.6fr; gap:12px; align-items:start; }
    .calculatievelden label { font-size:12px; min-width:0; }
    .calculatievelden input,.calculatievelden select { padding:10px 8px; font-size:13px; margin:6px 0 0; height:40px; min-width:0; }
    .dropdownactie { display:flex; align-items:end; gap:6px; }
    .dropdownactie select { flex:1; width:0; }
    .dropdownactie button { padding:10px; height:40px; font-size:12px; }
    .tags { display:flex; flex-wrap:wrap; gap:8px; margin:18px 0; }
    .tag { display:inline-flex; gap:9px; align-items:center; background:#e9f2d7; padding:7px 10px; border-radius:9px; font-size:13px; }
    .tag button { padding:1px 6px; background:transparent; color:#244c36; font-size:20px; }
    .actiebalk { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:18px 0; }
    .actiebalk a { color:#244c36; } .offerteknop { background:#47762e; }
    @media(max-width:1100px) { .calculatievelden { grid-template-columns:repeat(3,minmax(0,1fr)); } }
    @media(max-width:650px) { .calculatievelden { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media(max-width:420px) { .calculatievelden { grid-template-columns:1fr; } }
  </style>
</head>
<body class="{{ 'schema-pagina' if slug in ['productieschemas','maak-kostprijsberekening','klant-offerte'] else '' }}">
  <header>
    <a class="logo" href="{{ url_for('start') }}"><span class="merkteken">✿</span> Wouters<span style="color:#8cac48">.</span></a>
    <span class="portaal">Jouw werkportaal</span>
  </header>
  <main>
    <section class="hero">
      <p class="label">Kwekerij Wouters · Samen groeien</p>
      {% if onderdeel %}
        <h1>{{ onderdeel.naam }}</h1>
        <p class="intro">{{ onderdeel.tekst }}</p>
      {% else %}
        <h1>Alles begint met<br>een goed overzicht.</h1>
        <p class="intro">Van assortiment tot productie. Kies een onderdeel en ga direct aan de slag.</p>
      {% endif %}
    </section>
    {% if onderdeel %}
      <section class="inhoud">
        {% if fout %}<div class="melding fout" role="alert">{{ fout }}</div>{% endif %}
        {% if opgeslagen %}<div class="melding" role="status">Opgeslagen.</div>{% endif %}
        {% if slug in ['verkoopartikelen', 'opkweekverpakkingen'] %}
          <h2>Artikel toevoegen</h2>
          <form method="post">
            <label>Artikelnaam<input name="naam" required maxlength="200" value="{{ invoer.get('naam','') }}"></label>
            {% if slug == 'verkoopartikelen' %}
            <label>Substraat<select name="substraat_id">
              <option value="">Nog niet gekozen</option>
              {% for substraat in data.substraten %}<option value="{{ substraat.id }}" {% if invoer.get('actie') != 'substraat' and invoer.get('substraat_id') == substraat.id %}selected{% endif %}>{{ substraat.naam }}</option>{% endfor %}
            </select></label>
            {% endif %}
            {% if slug == 'opkweekverpakkingen' %}
            <div class="velden">
              <label>Prijs per 1000 stuks (€)<input name="prijs_per_1000" id="prijs" inputmode="decimal" required placeholder="Bijvoorbeeld 90,00" value="{{ invoer.get('prijs_per_1000','') }}"></label>
              <label>Inhoud grond per artikel (liter)<input name="inhoud_liter" inputmode="decimal" placeholder="Bijvoorbeeld 0,75" value="{{ invoer.get('inhoud_liter','') }}"></label>
              <label>Prijs per stuk<output id="stuk" style="display:block;padding:12px">€ 0,00000</output></label>
            </div>
            {% endif %}
            <button type="submit">Artikel toevoegen</button>
          </form>
          <h2>Huidige artikelen ({{ rijen|length }})</h2>
          <div class="tabel"><table><thead><tr><th>Artikel</th>{% if slug == 'verkoopartikelen' %}<th>Substraat</th>{% endif %}{% if slug == 'opkweekverpakkingen' %}<th>Prijs per 1000 (€)</th><th>Prijs per stuk</th><th>Inhoud grond (liter/artikel)</th><th>Actie</th>{% endif %}</tr></thead>
          <tbody>{% for a in rijen %}<tr><td>{{ a.naam }}</td>
          {% if slug == 'verkoopartikelen' %}
          {% set gekozen = invoer.get('substraat_id','') if fout and invoer.get('actie') == 'substraat' and invoer.get('id') == a.id else a.substraat_id %}
          <td><form method="post" class="substraatformulier">
            <input type="hidden" name="actie" value="substraat"><input type="hidden" name="id" value="{{ a.id }}">
            <select name="substraat_id" aria-label="Substraat voor {{ a.naam }}">
              <option value="">Nog niet gekozen</option>
              {% for substraat in data.substraten %}<option value="{{ substraat.id }}" {% if gekozen == substraat.id %}selected{% endif %}>{{ substraat.naam }}</option>{% endfor %}
            </select><button>Opslaan</button>
          </form></td>
          {% endif %}
          {% if slug == 'opkweekverpakkingen' %}
          {% set herhalen = fout and invoer.get('actie') == 'prijs' and invoer.get('id') == a.id %}
          <td><form id="verpakking-{{ a.id }}" method="post" class="prijsformulier"><input type="hidden" name="actie" value="prijs"><input type="hidden" name="id" value="{{ a.id }}"><input name="prijs_per_1000" value="{{ invoer.get('prijs_per_1000','') if herhalen else a.prijs_per_1000|string|replace('.',',') }}" inputmode="decimal" required aria-label="Prijs per 1000 voor {{ a.naam }}"></form></td>
          <td>{{ a.prijs_per_stuk|euro(5) }}</td>
          <td><input form="verpakking-{{ a.id }}" name="inhoud_liter" inputmode="decimal" placeholder="Nog invullen" aria-label="Inhoud grond in liter voor {{ a.naam }}" value="{{ invoer.get('inhoud_liter','') if herhalen else (a.inhoud_liter|string|replace('.',',') if a.inhoud_liter is not none else '') }}"></td>
          <td><button form="verpakking-{{ a.id }}">Opslaan</button></td>{% endif %}</tr>{% endfor %}</tbody></table></div>
        {% elif slug == 'substraten' %}
          <h2>Substraat toevoegen</h2>
          <form method="post">
            <div class="velden">
              <label>Naam substraat<input name="naam" required maxlength="200" value="{{ invoer.get('naam','') if invoer.get('actie') != 'prijs' else '' }}" placeholder="Bijvoorbeeld Violengrond"></label>
              <label>Prijs per liter (€)<input name="prijs_per_liter" inputmode="decimal" required value="{{ invoer.get('prijs_per_liter','') if invoer.get('actie') != 'prijs' else '' }}" placeholder="Bijvoorbeeld 10,00"></label>
            </div><button>Substraat toevoegen</button>
          </form>
          <h2>Huidige substraten ({{ rijen|length }})</h2>
          <div class="tabel"><table><thead><tr><th>Substraat</th><th>Prijs per liter (€)</th></tr></thead><tbody>
            {% for a in rijen %}<tr><td>{{ a.naam }}</td><td>
              <form method="post" class="prijsformulier">
                <input type="hidden" name="actie" value="prijs"><input type="hidden" name="id" value="{{ a.id }}">
                <input name="prijs_per_liter" required inputmode="decimal" aria-label="Prijs per liter voor {{ a.naam }}" value="{{ invoer.get('prijs_per_liter','') if fout and invoer.get('actie') == 'prijs' and invoer.get('id') == a.id else a.prijs_per_liter|string|replace('.',',') }}">
                <button>Opslaan</button>
              </form>
            </td></tr>{% endfor %}
          </tbody></table></div>
        {% elif slug == 'toegevoegde-waarde' %}
          <h2>Artikel toevoegen</h2>
          <form method="post">
            <div class="velden">
              <label>Artikelnaam<input name="naam" required maxlength="200" value="{{ invoer.get('naam','') if invoer.get('actie') != 'prijs' else '' }}" placeholder="Bijvoorbeeld Etiket"></label>
              <label>Prijs per stuk (€)<input name="prijs_per_stuk" inputmode="decimal" required value="{{ invoer.get('prijs_per_stuk','') if invoer.get('actie') != 'prijs' else '' }}" placeholder="Bijvoorbeeld 0,01"></label>
            </div><button>Artikel toevoegen</button>
          </form>
          <h2>Toegevoegde waarde artikelen ({{ rijen|length }})</h2>
          <div class="tabel"><table><thead><tr><th>Artikel</th><th>Prijs per stuk (€)</th></tr></thead><tbody>
            {% for a in rijen %}<tr><td>{{ a.naam }}</td><td>
              <form method="post" class="prijsformulier">
                <input type="hidden" name="actie" value="prijs"><input type="hidden" name="id" value="{{ a.id }}">
                <input name="prijs_per_stuk" required inputmode="decimal" aria-label="Prijs per stuk voor {{ a.naam }}" value="{{ invoer.get('prijs_per_stuk','') if fout and invoer.get('actie') == 'prijs' and invoer.get('id') == a.id else a.prijs_per_stuk|string|replace('.',',') }}">
                <button>Opslaan</button>
              </form>
            </td></tr>{% endfor %}
          </tbody></table></div>
        {% elif slug == 'klant-offerte' %}
          <h2>{{ offerte.projectnaam if offerte.projectnaam else 'Klant-offerte in voorbereiding' }}</h2>
          <p><strong>{{ offerte.klant }} / {{ offerte.eindklant }}</strong> · {{ offerte.regels|length }} artikelen</p>
          <div class="actiebalk"><a href="{{ url_for('overzicht', slug='maak-kostprijsberekening', offerte_id=offerte.id) }}">+ Nog een verkoopartikel berekenen</a><a href="{{ url_for('klant_offertes') }}">Mijn projecten en offertes</a></div>
          {% if request.args.get('bewaard') %}<div class="melding" role="status">Project opgeslagen. Je kunt het later terugvinden via Mijn projecten en offertes.</div>{% endif %}
          <div class="tabel"><table><thead><tr><th>Verkoopartikel</th><th>Aflevering</th><th>Teeltstart</th><th>Toegevoegde waarde</th><th>Aantal stuks</th><th>Kostprijs/plant</th><th>Totale kostprijs</th><th>Actie</th></tr></thead><tbody>
          {% for regel in offerte.regels %}{% set r = regel.berekening %}<tr><td>{{ r.verkoopartikel }}</td><td>{{ r.afleverjaar }}-W{{ '%02d'|format(r.afleverweek) }}</td><td>{{ r.startjaar }}-W{{ '%02d'|format(r.startweek) }}</td><td>{% for a in r.toevoegingen %}{{ a.naam }}{% if not loop.last %}, {% endif %}{% else %}Geen{% endfor %}</td><td>{{ r.aantal if r.get('aantal') else 'Niet vastgelegd' }}</td><td><strong>{{ r.totaal|euro(4) }}</strong></td><td>{{ r.totaal_order|euro(2) if r.get('aantal') else 'Niet berekend' }}</td><td><form method="post" action="{{ url_for('offerte_regel_verwijderen', offerte_id=offerte.id, regel_id=regel.id) }}" style="margin:0"><button>Verwijderen</button></form></td></tr>
          {% else %}<tr><td colspan="8">Nog geen artikelen toegevoegd.</td></tr>{% endfor %}
          </tbody></table></div>
          {% if offerte.regels %}
          <div class="melding"><strong>Totaal: {{ offertotalen.aantal }} stuks · {{ offertotalen.totaal|euro(2) }} kostprijs.</strong>
          {% if offertotalen.onbekend %}<p>{{ offertotalen.onbekend }} oudere regel(s) hebben nog geen vastgelegd aantal en tellen niet mee in dit totaal. Bereken deze artikelen opnieuw met het gewenste aantal.</p>{% endif %}</div>
          {% endif %}
          {% for regel in offerte.regels %}{% set r = regel.berekening %}<details style="margin-top:15px"><summary>{{ r.verkoopartikel }} · Kostenopbouw</summary><p>Pot: {{ r.pot.kosten|euro(6) }} · Substraat: {{ r.substraat.kosten|euro(6) }} · Teeltruimte: {{ r.teeltkosten|euro(6) }}{% for a in r.toevoegingen %} · {{ a.naam }}: {{ a.kosten|euro(6) }}{% endfor %}</p></details>{% endfor %}
          <p>De offerte toont de kostprijs per plant en de totale kostprijs voor het ingevoerde aantal. De bewaarde berekeningen behouden de prijzen en uitgangspunten van het moment van toevoegen. Plantmateriaal, arbeid, meststoffen en uitval zijn nog niet opgenomen; er is geen verkoopmarge toegevoegd.</p>
          <form method="post"><label>Projectnaam<input name="projectnaam" required maxlength="200" placeholder="Bijvoorbeeld Welkoop voorjaar 2027" value="{{ invoer.get('projectnaam', offerte.projectnaam) }}"></label><button>Project opslaan</button></form>
        {% elif slug == 'klant-offertes' %}
          <h2>Mijn projecten en offertes</h2>
          <div class="actiebalk"><a href="{{ url_for('overzicht', slug='maak-kostprijsberekening') }}">+ Nieuwe klant-offerte beginnen</a></div>
          <div class="tabel"><table><thead><tr><th>Projectnaam</th><th>Klant</th><th>Eindklant</th><th>Artikelen</th><th>Status</th></tr></thead><tbody>{% for o in offertes %}<tr><td><a href="{{ url_for('klant_offerte', offerte_id=o.id) }}">{{ o.projectnaam or 'Naamloos concept' }}</a></td><td>{{ o.klant }}</td><td>{{ o.eindklant }}</td><td>{{ o.regels|length }}</td><td>{{ 'Opgeslagen project' if o.projectnaam else 'Concept' }}</td></tr>{% else %}<tr><td colspan="5">Er zijn nog geen projecten of offertes.</td></tr>{% endfor %}</tbody></table></div>
        {% elif slug == 'maak-kostprijsberekening' %}
          <h2>Kostprijs voor klant / eindklant</h2>
          {% if request.args.get('toegevoegd') %}<div class="melding" role="status">Artikel toegevoegd aan de klant-offerte. Kies het volgende verkoopartikel.</div>{% endif %}
          <div class="actiebalk">
            {% if offerte %}<a href="{{ url_for('klant_offerte', offerte_id=offerte.id) }}"><strong>Bekijk klant-offerte ({{ offerte.regels|length }} artikelen)</strong></a>{% endif %}
            <a href="{{ url_for('klant_offertes') }}">Mijn projecten en offertes</a>
            <a href="{{ url_for('overzicht', slug='maak-kostprijsberekening') }}">Nieuwe klant-offerte beginnen</a>
          </div>
          <form method="post" id="calculatieformulier" data-resultaat="{{ '1' if resultaat else '0' }}">
            <input type="hidden" name="offerte_id" value="{{ offerte.id if offerte else '' }}">
            <input type="hidden" name="berekening_token" value="{{ berekening_token }}">
            <div class="calculatievelden">
              <label>Klant<input name="klant" list="klanten" required maxlength="200" value="{{ invoer.get('klant','') }}" placeholder="Green4Retail"></label>
              <label>Eindklant<input name="eindklant" list="eindklanten" required maxlength="200" value="{{ invoer.get('eindklant','') }}" placeholder="Welkoop"></label>
              <label>Verkoopartikel<select name="verkoop_id" required><option value="">Kies een verkoopartikel</option>{% for a in data.verkoopartikelen %}<option value="{{ a.id }}" {% if invoer.get('verkoop_id') == a.id %}selected{% endif %}>{{ a.naam }}</option>{% endfor %}</select></label>
              <label>Aantal stuks<input name="aantal" type="number" min="1" max="1000000000" step="1" required value="{{ invoer.get('aantal','1') }}"></label>
              <label>Afleverjaar<input name="jaar" type="number" min="1900" max="9999" required value="{{ invoer.get('jaar', huidig_jaar) }}"></label>
              <label>Afleverweek<input name="week" type="number" min="1" max="53" required value="{{ invoer.get('week','') }}" placeholder="20"></label>
              <div><label for="waarde-keuze">Toegevoegde waarde</label><div class="dropdownactie">
                <select id="waarde-keuze"><option value="">Kies een artikel</option>{% for a in data['toegevoegde-waarde'] %}<option value="{{ a.id }}">{{ a.naam }} · {{ a.prijs_per_stuk|euro(4) }}</option>{% endfor %}</select>
                <button type="button" id="waarde-toevoegen">Toevoegen</button>
              </div></div>
            </div>
            <datalist id="klanten"><option value="Green4Retail"><option value="Salomo"></datalist>
            <datalist id="eindklanten"><option value="Welkoop"><option value="Online"><option value="Netto"></datalist>
            <div id="gekozen-waarden" class="tags" aria-live="polite"></div>
            <p style="font-size:13px">Elke toegevoegde waarde telt één keer per plant. Verwijder een keuze met ×. Na een berekening worden wijzigingen in toegevoegde waarde direct doorgerekend.</p>
            <button>Bereken kostprijs</button>
          </form>
          {% if resultaat %}
          <section aria-label="Resultaat kostprijsberekening">
            <h2>{{ resultaat.klant }} / {{ resultaat.eindklant }}</h2>
            <h3>{{ resultaat.verkoopartikel }}</h3>
            <p>Aflevering: ISO {{ resultaat.afleverjaar }}-W{{ '%02d'|format(resultaat.afleverweek) }} · Teeltstart: ISO {{ resultaat.startjaar }}-W{{ '%02d'|format(resultaat.startweek) }} · {{ resultaat.totaal_weken }} teeltweken</p>
            <p>De afleverweek telt mee als laatste teeltweek. De fasen sluiten op elkaar aan, zonder overlap.</p>
            <div class="tabel"><table><thead><tr><th>Kostenpost</th><th>Onderbouwing per plant</th><th>Kosten per plant</th></tr></thead><tbody>
              <tr><td>Opkweekverpakking</td><td>{{ resultaat.pot.naam }} · {{ resultaat.pot.prijs_per_1000|euro(4) }} / 1000</td><td>{{ resultaat.pot.kosten|euro(6) }}</td></tr>
              <tr><td>Substraat</td><td>{{ resultaat.substraat.naam }} · {{ resultaat.substraat.liters }} liter × {{ resultaat.substraat.prijs_per_liter|euro(4) }}/liter</td><td>{{ resultaat.substraat.kosten|euro(6) }}</td></tr>
              <tr><td>Teeltruimte</td><td>Som van weekprijs per m² ÷ planten per m² in die fase</td><td>{{ resultaat.teeltkosten|euro(6) }}</td></tr>
              {% for a in resultaat.toevoegingen %}<tr><td>Toegevoegde waarde</td><td>{{ a.naam }} · 1 stuk</td><td>{{ a.kosten|euro(6) }}</td></tr>{% endfor %}
              <tr style="font-weight:bold;background:#e9f2d7"><td>Totaal per plant</td><td>Som van bovenstaande posten</td><td>{{ resultaat.totaal|euro(6) }}</td></tr>
            </tbody></table></div>
            <div class="melding"><strong>{{ resultaat.aantal }} stuks × {{ resultaat.totaal|euro(4) }} per plant = {{ resultaat.totaal_order|euro(2) }} totale kostprijs.</strong></div>
            <p> Dit omvat pot, substraat, teeltruimte en geselecteerde toegevoegde waarde. Overige kosten zoals plantmateriaal, arbeid, meststoffen en uitval zijn nog niet opgenomen.</p>
            <div class="actiebalk"><button class="offerteknop" id="voeg-offerte-toe" form="calculatieformulier" formaction="{{ url_for('offerte_toevoegen') }}">Voeg toe aan klant-offerte</button><span>Bewaar deze berekening en ga door met het volgende artikel.</span></div>
            <h3>Teeltfasen</h3>
            <div class="tabel"><table><thead><tr><th>Fase</th><th>Start ISO-jaar/week</th><th>Einde ISO-jaar/week</th><th>Weken</th><th>Planten/m²</th><th>Kosten/plant</th></tr></thead><tbody>{% for f in resultaat.fasen %}<tr><td>{{ f.naam|capitalize }}</td><td>{{ f.start }}</td><td>{{ f.einde }}</td><td>{{ f.weken }}</td><td>{{ f.planten_per_m2 }}</td><td>{{ f.kosten|euro(6) }}</td></tr>{% endfor %}</tbody></table></div>
            <details style="margin-top:20px"><summary>Bekijk de berekening per ISO-week</summary><p>De weekprijslijst geldt voor alle jaren. Er wordt gerekend met de werkelijk doorlopen ISO-weken, inclusief week 53 als die voorkomt.</p><div class="tabel"><table><thead><tr><th>ISO-jaar</th><th>Week</th><th>Fase</th><th>Prijs/m²</th><th>Planten/m²</th><th>Kosten/plant</th></tr></thead><tbody>{% for w in resultaat.weken %}<tr><td>{{ w.jaar }}</td><td>{{ w.week }}</td><td>{{ w.fase|capitalize }}</td><td>{{ w.prijs_per_m2|euro(4) }}</td><td>{{ w.planten_per_m2 }}</td><td>{{ w.kosten|euro(6) }}</td></tr>{% endfor %}</tbody></table></div></details>
          </section>
          {% endif %}
        {% elif slug == 'prijs-per-m2' %}
          <h2>Kostprijs per m² per ISO-week</h2>
          <p>Vul per week de kostprijs in euro per m² in. Klik op Alle weekprijzen opslaan om je wijzigingen te bewaren.</p>
          <p>Dit is één weekprijslijst zonder jaarkeuze. Week 53 is optioneel voor ISO-jaren die 53 weken hebben.</p>
          <form method="post">
            <button>Alle weekprijzen opslaan</button>
            <div class="weekgrid">
              {% for rij in rijen %}
              {% set veld = 'week_' ~ rij.week %}
              <label class="weekkaart">ISO-week {{ '%02d'|format(rij.week) }}{% if rij.week == 53 %} <small>(optioneel)</small>{% endif %}
                <small style="display:block">Kostprijs €/m²</small>
                <input name="{{ veld }}" inputmode="decimal" {% if rij.week <= 52 %}required{% endif %} aria-label="Kostprijs in euro per m² ISO-week {{ rij.week }}" placeholder="{{ 'Niet ingevuld' if rij.week == 53 else 'Bijvoorbeeld 10,00' }}" value="{{ invoer.get(veld,'') if fout else (rij.prijs_per_m2|string|replace('.',',') if rij.prijs_per_m2 is not none else '') }}">
              </label>
              {% endfor %}
            </div>
            <button>Alle weekprijzen opslaan</button>
          </form>
        {% elif slug == 'productieschemas' %}
          <h2>Productieschema’s</h2>
          <p>Pas de waarden direct in de regel aan en klik op Opslaan. Voeg onderaan een nieuw schema toe. Een fase overslaan? Vul bij beide kolommen 0 in.</p>
          {% macro schemavelden(form_id, schema, bewerken) %}
            {% set herhalen = fout and invoer.get('actie', 'toevoegen') == ('schema_opslaan' if bewerken else 'toevoegen') and (not bewerken or invoer.get('id') == schema.id) %}
            <td><input class="schemanaam" form="{{ form_id }}" name="naam" required maxlength="200" aria-label="Naam productieschema" placeholder="Nieuw productieschema" value="{{ invoer.get('naam','') if herhalen else (schema.naam if bewerken else '') }}"></td>
            {% for fase in fasen %}
              {% for veld in ['weken', 'planten_per_m2'] %}
                {% set sleutel = fase ~ '_' ~ veld %}
                <td><input form="{{ form_id }}" name="{{ sleutel }}" {% if veld == 'weken' %}type="number" min="0" max="1000000000" step="1"{% else %}inputmode="decimal"{% endif %} required aria-label="{{ schema.naam if bewerken else 'Nieuw schema' }} {{ fase }} {{ 'weken' if veld == 'weken' else 'planten per m²' }}" value="{{ invoer.get(sleutel,'0') if herhalen else (schema.fasen[fase][veld] if bewerken else '0') }}"></td>
              {% endfor %}
            {% endfor %}
          {% endmacro %}
          {% for schema in rijen %}
          <form method="post" id="schema-{{ schema.id }}" style="margin:0"><input type="hidden" name="actie" value="schema_opslaan"><input type="hidden" name="id" value="{{ schema.id }}"></form>
          {% endfor %}
          <form method="post" id="nieuw-schema" style="margin:0"><input type="hidden" name="actie" value="toevoegen"></form>
          <div class="tabel" role="region" aria-label="Productieschema’s, scroll horizontaal voor alle kolommen" tabindex="0">
            <table class="schematabel"><thead><tr><th scope="col">Productieschema</th>
              {% for fase in fasen %}<th scope="col">{{ fase|capitalize }} weken</th><th scope="col">{{ fase|capitalize }} p/p/m²</th>{% endfor %}
              <th scope="col">Actie</th></tr></thead><tbody>
              {% for schema in rijen %}<tr>{{ schemavelden('schema-' ~ schema.id, schema, true) }}<td><button form="schema-{{ schema.id }}">Opslaan</button></td></tr>{% endfor %}
              <tr class="nieuw">{{ schemavelden('nieuw-schema', none, false) }}<td><button form="nieuw-schema">Toevoegen</button></td></tr>
            </tbody></table>
          </div>
        {% elif slug == 'vertaling-verkoop-inkoop' %}
          <h2>Artikelen koppelen</h2>
          <form method="post"><div class="velden">
            <label>Verkoopartikel<select name="verkoop_id" required>{% for a in data.verkoopartikelen %}<option value="{{ a.id }}" {% if invoer.get('verkoop_id') == a.id %}selected{% endif %}>{{ a.naam }}</option>{% endfor %}</select></label>
            <label>Inkoopartikel<select name="inkoop_id" required>{% for a in data.opkweekverpakkingen %}<option value="{{ a.id }}" {% if invoer.get('inkoop_id') == a.id %}selected{% endif %}>{{ a.naam }}</option>{% endfor %}</select></label>
          </div><button>Koppeling toevoegen</button></form>
          <p>Een verkoopartikel kan aan meerdere opkweekverpakkingen worden gekoppeld.</p>
          <div class="tabel"><table><thead><tr><th>Verkoopartikel</th><th>Inkoopartikel</th><th>Prijs per stuk</th></tr></thead><tbody>{% for k in rijen %}<tr><td>{{ k.verkoopartikel }}</td><td>{{ k.inkoopartikel }}</td><td>{{ k.prijs_per_stuk|euro(5) }}</td></tr>{% endfor %}</tbody></table></div>
        {% endif %}
        <a class="terug" href="{{ url_for('start') }}">← Terug naar de startpagina</a>
      </section>
    {% else %}
      <div class="sectiekop"><h2>Waar wil je naartoe?</h2><span class="hint">Acht onderdelen. Eén overzicht.</span></div>
      <p><a href="{{ url_for('klant_offertes') }}" style="color:var(--groen)">Mijn projecten en offertes →</a></p>
      <nav class="grid" aria-label="Onderdelen">
        {% for sleutel, item in onderdelen.items() %}
        <a class="kaart" href="{{ url_for('overzicht', slug=sleutel) }}">
          <div class="kaartboven"><span class="icoon {{ item.kleur }}" aria-hidden="true">{{ item.icoon }}</span><span class="nummer">{{ item.nummer }}</span></div>
          <h3>{{ item.naam }}</h3>
          <p>{{ item.tekst }}</p>
          <span class="open">Open overzicht <span class="pijl" aria-hidden="true">↗</span></span>
        </a>
        {% endfor %}
      </nav>
    {% endif %}
    <footer><span>Kwekerij Wouters</span><span>Assortiment · Inkoop · Productie</span></footer>
  </main>
<script>
const prijs = document.getElementById('prijs');
if (prijs) {
  const update = () => {
    const tekst = prijs.value.trim().replace(',', '.');
    const waarde = Number(tekst);
    document.getElementById('stuk').textContent = tekst && Number.isFinite(waarde) && waarde >= 0
      ? (waarde / 1000).toLocaleString('nl-NL', {style:'currency', currency:'EUR', minimumFractionDigits:5, maximumFractionDigits:5})
      : 'Vul een geldige prijs in';
  };
  prijs.addEventListener('input', update); update();
}
const calc = document.getElementById('calculatieformulier');
if (calc) {
  const opties = {{ data['toegevoegde-waarde']|tojson if slug == 'maak-kostprijsberekening' else '[]' }};
  const gekozen = new Set({{ gekozen_toevoegingen|tojson if slug == 'maak-kostprijsberekening' else '[]' }});
  const lijst = document.getElementById('gekozen-waarden');
  const keuze = document.getElementById('waarde-keuze');
  const tekenen = () => {
    lijst.replaceChildren();
    for (const id of gekozen) {
      const artikel = opties.find(a => a.id === id);
      if (!artikel) continue;
      const chip = document.createElement('span'); chip.className = 'tag';
      const tekst = document.createElement('span'); tekst.textContent = artikel.naam;
      const hidden = document.createElement('input'); hidden.type = 'hidden'; hidden.name = 'toevoegingen'; hidden.value = id;
      const weg = document.createElement('button'); weg.type = 'button'; weg.textContent = '×'; weg.setAttribute('aria-label', 'Verwijder ' + artikel.naam);
      weg.addEventListener('click', () => { gekozen.delete(id); tekenen(); herbereken(); });
      chip.append(tekst, hidden, weg); lijst.append(chip);
    }
    for (const optie of keuze.options) optie.disabled = gekozen.has(optie.value);
  };
  const verouderd = () => {
    const toevoegen = document.getElementById('voeg-offerte-toe');
    if (toevoegen) toevoegen.hidden = true;
  };
  const herbereken = () => {
    verouderd();
    if (calc.dataset.resultaat === '1' && calc.reportValidity()) calc.requestSubmit();
  };
  document.getElementById('waarde-toevoegen').addEventListener('click', () => {
    if (!keuze.value) { keuze.focus(); return; }
    gekozen.add(keuze.value); keuze.value = ''; tekenen(); herbereken();
  });
  calc.addEventListener('input', verouderd);
  calc.addEventListener('change', (event) => { if (event.target !== keuze) verouderd(); });
  tekenen();
}
</script>
</body>
</html>
"""


HERSTELPAGINA = """
<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gegevens herstellen | Wouters</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f6f7f2;color:#23382c;font:16px/1.6 Arial,sans-serif}
main{max-width:760px;margin:60px auto;padding:32px;background:white;border:1px solid #e1e7dc;border-radius:22px}
h1{color:#244c36;line-height:1.2}button{padding:14px 20px;border:0;border-radius:10px;background:#244c36;color:white;font:inherit;cursor:pointer}
code{background:#f1f5eb;padding:3px 5px;overflow-wrap:anywhere}.melding{background:#fff2cb;padding:16px;border-radius:10px}
a{color:#244c36}@media(max-width:800px){main{margin:20px}}
</style></head><body><main>
<p>KWEKERIJ WOUTERS</p><h1>Het gegevensbestand kan niet worden gelezen</h1>
<p><code>wouters_data.json</code> is leeg of bevat ongeldige JSON. Je hoeft Flask niet opnieuw te installeren.</p>
{% if fout %}<p class="melding" role="alert">{{ fout }}</p>{% endif %}
<p>Met de knop hieronder zet je alle vooraf ingevulde artikelen, prijzen, productieschema’s, substraten en koppelingen terug.</p>
<p class="melding"><strong>Zelf ingevoerde wijzigingen worden niet automatisch hersteld.</strong>
Eerst wordt een kopie van het huidige bestand bewaard in dezelfde map als
<code>wouters_data_beschadigd_….json</code>. Deze kopie blijft beschikbaar om later gegevens uit te halen.</p>
<form method="post" action="{{ url_for('gegevens_herstellen') }}"><button>Maak een kopie en zet standaardgegevens terug</button></form>
<p>Wil je eerst je eigen gegevens herstellen? Laat het bestand dan staan en deel <code>wouters_data.json</code>, zodat de inhoud gecontroleerd kan worden.</p>
<a href="{{ url_for('start') }}">Terug naar de startpagina</a>
</main></body></html>
"""


@app.errorhandler(json.JSONDecodeError)
@app.errorhandler(UnicodeError)
def onleesbare_gegevens(error):
    if request.path.startswith("/api/"):
        return jsonify({"fout": "Het gegevensbestand is leeg of ongeldig.",
                        "herstelpagina": url_for("gegevens_herstellen")}), 503
    return render_template_string(HERSTELPAGINA, fout=None), 503


@app.route("/gegevens-herstellen", methods=["GET", "POST"])
def gegevens_herstellen():
    with LOCK:
        try:
            lezen()
        except (json.JSONDecodeError, UnicodeError):
            if request.method == "GET":
                return render_template_string(HERSTELPAGINA, fout=None), 503
            # Kopieer het originele bestand voordat standaardgegevens worden geschreven.
            # Als de kopie mislukt, wordt het origineel niet vervangen.
            backup = DATA_FILE.with_name(DATA_FILE.stem + "_beschadigd_" + uuid4().hex + ".json")
            try:
                shutil.copy2(DATA_FILE, backup)
                bewaren(begindata())
            except OSError:
                app.logger.exception("Herstel van het gegevensbestand is mislukt")
                return render_template_string(
                    HERSTELPAGINA,
                    fout="Herstellen is niet gelukt. Controleer of je in deze map bestanden mag opslaan en probeer opnieuw.",
                ), 503
            return redirect(url_for("herstel_gelukt"))
        # Een geldig bestand wordt nooit teruggezet naar standaardgegevens.
        return redirect(url_for("start"))


@app.get("/herstel-gelukt")
def herstel_gelukt():
    return render_template_string("""
    <!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Herstel afgerond</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="font:18px/1.6 Arial,sans-serif;max-width:700px;margin:50px auto;padding:20px;color:#244c36">
    <h1>Standaardgegevens teruggezet</h1>
    <p>Een kopie van het oude bestand staat naast app.py onder de naam wouters_data_beschadigd_….json.</p>
    <p>Eigen wijzigingen uit het oude bestand zijn niet automatisch hersteld.</p>
    <a href="{{ url_for('start') }}">Open de startpagina</a></body></html>
    """)


@app.route("/")
def start():
    return render_template_string(PAGINA, onderdelen=ONDERDELEN, onderdeel=None)


@app.route("/<slug>", methods=["GET", "POST"])
def overzicht(slug):
    if slug == "maak-kostprijsberekening":
        return kostprijs_pagina()
    if slug == "inkoopartikelen":
        if request.method == "GET":
            return redirect(url_for("overzicht", slug="opkweekverpakkingen"))
        slug = "opkweekverpakkingen"
    onderdeel = ONDERDELEN.get(slug)
    if onderdeel is None:
        abort(404)
    fout = None
    with LOCK:
        data = lezen()
        if request.method == "POST":
            try:
                if slug == "verkoopartikelen" and request.form.get("actie") == "substraat":
                    artikel = next((a for a in data[slug] if a["id"] == request.form.get("id")), None)
                    if artikel is None:
                        raise ValueError("Dit verkoopartikel bestaat niet.")
                    artikel["substraat_id"] = substraat_invoer(data)
                elif slug in ("verkoopartikelen", "opkweekverpakkingen"):
                    if slug == "opkweekverpakkingen" and request.form.get("actie") == "prijs":
                        artikel = next((a for a in data[slug] if a["id"] == request.form.get("id")), None)
                        if artikel is None:
                            raise ValueError("Deze opkweekverpakking bestaat niet.")
                        artikel["prijs_per_1000"] = getal(request.form.get("prijs_per_1000", ""), "prijs per 1000")
                        if "inhoud_liter" in request.form:
                            artikel["inhoud_liter"] = inhoud_invoer()
                    else:
                        naam = naam_invoer()
                        if any(a["naam"].casefold() == naam.casefold() for a in data[slug]):
                            raise ValueError("Dit artikel bestaat al.")
                        artikel = {"id": uuid4().hex, "naam": naam}
                        if slug == "verkoopartikelen":
                            artikel["substraat_id"] = substraat_invoer(data)
                        if slug == "opkweekverpakkingen":
                            artikel["prijs_per_1000"] = getal(request.form.get("prijs_per_1000", ""), "prijs per 1000")
                            artikel["inhoud_liter"] = inhoud_invoer()
                        data[slug].append(artikel)
                elif slug == "substraten":
                    prijs = getal(request.form.get("prijs_per_liter", ""), "prijs per liter")
                    if request.form.get("actie") == "prijs":
                        substraat = next((a for a in data[slug] if a["id"] == request.form.get("id")), None)
                        if substraat is None:
                            raise ValueError("Dit substraat bestaat niet.")
                        substraat["prijs_per_liter"] = prijs
                    else:
                        naam = naam_invoer()
                        if any(a["naam"].casefold() == naam.casefold() for a in data[slug]):
                            raise ValueError("Dit substraat bestaat al.")
                        data[slug].append({"id": uuid4().hex, "naam": naam, "prijs_per_liter": prijs})
                elif slug == "toegevoegde-waarde":
                    prijs = getal(request.form.get("prijs_per_stuk", ""), "prijs per stuk")
                    if request.form.get("actie") == "prijs":
                        artikel = next((a for a in data[slug] if a["id"] == request.form.get("id")), None)
                        if artikel is None:
                            raise ValueError("Dit artikel bestaat niet.")
                        artikel["prijs_per_stuk"] = prijs
                    else:
                        naam = naam_invoer()
                        if any(a["naam"].casefold() == naam.casefold() for a in data[slug]):
                            raise ValueError("Dit artikel bestaat al.")
                        data[slug].append({"id": uuid4().hex, "naam": naam, "prijs_per_stuk": prijs})
                elif slug == "prijs-per-m2":
                    prijzen = []
                    for week in range(1, 54):
                        waarde = request.form.get("week_" + str(week), "").strip()
                        prijs = None if week == 53 and not waarde else getal(waarde, f"kostprijs per m² week {week}")
                        prijzen.append({"week": week, "prijs_per_m2": prijs})
                    # Bewaar alleen wanneer de volledige invoer geldig is.
                    data[slug] = prijzen
                elif slug == "productieschemas":
                    naam = naam_invoer()
                    bewerken = request.form.get("actie") == "schema_opslaan"
                    schema_id = request.form.get("id") if bewerken else uuid4().hex
                    bestaand = next((a for a in data[slug] if a["id"] == schema_id), None)
                    if bewerken and bestaand is None:
                        raise ValueError("Dit productieschema bestaat niet meer. Vernieuw de pagina.")
                    if any(a["naam"].casefold() == naam.casefold() and a["id"] != schema_id for a in data[slug]):
                        raise ValueError("Deze schemanaam bestaat al.")
                    fasen = {}
                    for fase in FASEN:
                        weken = getal(request.form.get(fase + "_weken", ""), fase + " weken", geheel=True)
                        dichtheid = getal(request.form.get(fase + "_planten_per_m2", ""), fase + " planten per m²")
                        if (weken > 0) != (dichtheid > 0):
                            raise ValueError(f"{fase.capitalize()}: vul weken én plantdichtheid positief in, of zet beide op 0.")
                        fasen[fase] = {"weken": weken, "planten_per_m2": dichtheid}
                    if not any(f["weken"] for f in fasen.values()):
                        raise ValueError("Vul ten minste één productiefase in.")
                    if bewerken:
                        bestaand.update(naam=naam, fasen=fasen)
                    else:
                        data[slug].append({"id": schema_id, "naam": naam, "fasen": fasen})
                else:
                    koppeling = {"verkoop_id": request.form.get("verkoop_id"), "inkoop_id": request.form.get("inkoop_id")}
                    for veld, bron in [("verkoop_id", "verkoopartikelen"), ("inkoop_id", "opkweekverpakkingen")]:
                        if not any(a["id"] == koppeling[veld] for a in data[bron]):
                            raise ValueError("Selecteer bestaande artikelen.")
                    if koppeling in data[slug]:
                        raise ValueError("Deze koppeling bestaat al.")
                    data[slug].append(koppeling)
                bewaren(data)
                return redirect(url_for("overzicht", slug=slug, opgeslagen="1"))
            except ValueError as exc:
                fout = str(exc)
                data = lezen()
    return render_template_string(
        PAGINA, onderdelen=ONDERDELEN, onderdeel=onderdeel, slug=slug,
        data=data, rijen=verrijk(data, slug), fasen=FASEN, fout=fout,
        opgeslagen=request.args.get("opgeslagen") == "1" and not fout,
        invoer=request.form if fout else {},
    ), 400 if fout else 200


@app.get("/api/<slug>")
def api_overzicht(slug):
    if slug == "inkoopartikelen":
        slug = "opkweekverpakkingen"
    if slug not in ONDERDELEN:
        return jsonify({"fout": "Onbekend onderdeel"}), 404
    if slug == "maak-kostprijsberekening":
        return jsonify({"omschrijving": "Bereken via POST op dit endpoint.",
                        "velden": ["klant", "eindklant", "verkoop_id", "aantal", "jaar", "week", "toevoegingen"],
                        "voorbeeldklanten": [{"klant": "Green4Retail", "eindklant": "Welkoop"},
                                             {"klant": "Salomo", "eindklant": "Online"},
                                             {"klant": "Salomo", "eindklant": "Netto"}]})
    response = jsonify(verrijk(lezen(), slug))
    response.headers["Cache-Control"] = "no-store"
    return response


def dec(waarde, label):
    # Gebruik Decimal voor alle berekeningen; rond pas af bij de weergave.
    getal(waarde, label)
    return Decimal(str(waarde).replace(",", "."))


def bereken_kostprijs(data, invoer, toevoegingen):
    klant = str(invoer.get("klant", "")).strip()
    eindklant = str(invoer.get("eindklant", "")).strip()
    if not klant or not eindklant or len(klant) > 200 or len(eindklant) > 200:
        raise ValueError("Vul klant en eindklant in (maximaal 200 tekens per veld).")
    aantal_stuks = getal(invoer.get("aantal", ""), "aantal stuks", geheel=True)
    if aantal_stuks < 1:
        raise ValueError("Vul minimaal 1 stuk in.")
    jaar = getal(invoer.get("jaar", ""), "afleverjaar", geheel=True)
    week = getal(invoer.get("week", ""), "afleverweek", geheel=True)
    if not 1900 <= jaar <= 9999:
        raise ValueError("Kies een afleverjaar tussen 1900 en 9999.")
    try:
        afleverdatum = date.fromisocalendar(jaar, week, 1)
    except ValueError:
        raise ValueError("Deze ISO-week bestaat niet in het gekozen afleverjaar.")
    artikel = next((a for a in data["verkoopartikelen"] if a["id"] == invoer.get("verkoop_id")), None)
    if artikel is None:
        raise ValueError("Kies een bestaand verkoopartikel.")
    fouten = []
    substraat = next((a for a in data["substraten"] if a["id"] == artikel.get("substraat_id")), None)
    if substraat is None:
        fouten.append("Selecteer en bewaar eerst een substraat voor dit artikel op Verkoopartikelen.")
    koppelingen = [k for k in data["vertaling-verkoop-inkoop"] if k["verkoop_id"] == artikel["id"]]
    pot = None
    if len(koppelingen) != 1:
        fouten.append("Koppel precies één opkweekverpakking aan dit verkoopartikel bij Vertaling verkoop-inkoop; de potkeuze is nu ontbrekend of niet eenduidig.")
    else:
        pot = next((p for p in data["opkweekverpakkingen"] if p["id"] == koppelingen[0]["inkoop_id"]), None)
        if pot is None:
            fouten.append("De gekoppelde opkweekverpakking bestaat niet meer.")
        elif pot.get("inhoud_liter") is None or dec(pot["inhoud_liter"], "inhoud grond") <= 0:
            fouten.append("Vul bij Opkweekverpakkingen een inhoud grond groter dan 0 liter in voor deze pot.")
    schemas = [s for s in data["productieschemas"] if s["naam"].strip().casefold() == artikel["naam"].strip().casefold()]
    if len(schemas) != 1:
        fouten.append("Maak een productieschema met precies dezelfde naam als het verkoopartikel.")
    if fouten:
        raise ValueError(" ".join(fouten))
    schema = schemas[0]
    totaal_weken = 0
    fases = []
    for fase in FASEN:
        f = schema["fasen"][fase]
        aantal = getal(f["weken"], fase + " weken", geheel=True)
        dichtheid = dec(f["planten_per_m2"], fase + " planten per m²")
        if aantal and dichtheid <= 0:
            raise ValueError(f"Vul een positieve plantdichtheid in voor {fase}.")
        totaal_weken += aantal
        fases.append((fase, aantal, dichtheid))
    if not 1 <= totaal_weken <= 520:
        raise ValueError("Gebruik een productieschema met in totaal 1 tot 520 weken.")
    try:
        start = afleverdatum - timedelta(weeks=totaal_weken - 1)
    except OverflowError:
        raise ValueError("De teeltstart ligt buiten het ondersteunde datumbereik.")
    prijzen = {p["week"]: p["prijs_per_m2"] for p in data["prijs-per-m2"]}
    weken = []
    fase_resultaten = []
    cursor = start
    teeltkosten = Decimal(0)
    for fase, aantal, dichtheid in fases:
        begin = cursor
        kosten = Decimal(0)
        for offset in range(aantal):
            datum = begin + timedelta(weeks=offset)
            isojaar, isoweek, _ = datum.isocalendar()
            prijs = prijzen.get(isoweek)
            if prijs is None:
                raise ValueError(f"Vul eerst de prijs per m² voor ISO-week {isoweek} in op Prijs per m2 (nodig voor {isojaar}).")
            prijs = dec(prijs, f"prijs per m² week {isoweek}")
            weekkosten = prijs / dichtheid
            kosten += weekkosten
            weken.append({"jaar": isojaar, "week": isoweek, "fase": fase,
                          "prijs_per_m2": float(prijs), "planten_per_m2": float(dichtheid), "kosten": float(weekkosten)})
        einde = begin + timedelta(weeks=aantal - 1) if aantal else None
        label = lambda d: f"{d.isocalendar().year}-W{d.isocalendar().week:02}" if d else "Overgeslagen"
        fase_resultaten.append({"naam": fase, "weken": aantal, "planten_per_m2": float(dichtheid),
                                "start": label(begin if aantal else None), "einde": label(einde), "kosten": float(kosten)})
        teeltkosten += kosten
        # De cursor hoeft na de laatste fase niet voorbij het ondersteunde jaar te gaan.
        if fase != FASEN[-1]:
            cursor = begin + timedelta(weeks=aantal)
    if not isinstance(toevoegingen, list) or any(not isinstance(i, str) for i in toevoegingen):
        raise ValueError("Geef toegevoegde waarde op als lijst met artikel-ID’s.")
    gekozen_ids = set(toevoegingen)
    opties = {a["id"]: a for a in data["toegevoegde-waarde"]}
    if gekozen_ids - opties.keys():
        raise ValueError("Een gekozen toegevoegde waarde artikel bestaat niet meer.")
    extra = [{"naam": a["naam"], "kosten": float(dec(a["prijs_per_stuk"], "prijs toegevoegde waarde"))}
             for a in data["toegevoegde-waarde"] if a["id"] in gekozen_ids]
    potkosten = dec(pot["prijs_per_1000"], "potprijs") / 1000
    liters = dec(pot["inhoud_liter"], "liters grond")
    grondprijs = dec(substraat["prijs_per_liter"], "substraatprijs")
    grondkosten = liters * grondprijs
    extra_kosten = sum((dec(opties[i]["prijs_per_stuk"], "prijs toegevoegde waarde") for i in gekozen_ids), Decimal(0))
    totaal = potkosten + grondkosten + teeltkosten + extra_kosten
    return {"klant": klant, "eindklant": eindklant, "verkoopartikel": artikel["naam"],
            "aantal": aantal_stuks, "totaal_order": float(totaal * aantal_stuks),
            "afleverjaar": jaar, "afleverweek": week, "startjaar": start.isocalendar().year,
            "startweek": start.isocalendar().week, "totaal_weken": totaal_weken,
            "pot": {"naam": pot["naam"], "prijs_per_1000": pot["prijs_per_1000"], "kosten": float(potkosten)},
            "substraat": {"naam": substraat["naam"], "liters": float(liters), "prijs_per_liter": float(grondprijs), "kosten": float(grondkosten)},
            "teeltkosten": float(teeltkosten), "toevoegingen": extra, "totaal": float(totaal),
            "fasen": fase_resultaten, "weken": weken}


def zoek_offerte(data, offerte_id):
    offerte = next((o for o in data["klant-offertes"] if o["id"] == offerte_id), None)
    if offerte is None:
        abort(404)
    return offerte


def toon_calculatie(data, invoer, gekozen, resultaat=None, fout=None, offerte=None, token=None):
    return render_template_string(
        PAGINA, onderdelen=ONDERDELEN, onderdeel=ONDERDELEN["maak-kostprijsberekening"],
        slug="maak-kostprijsberekening", data=data, fout=fout, opgeslagen=False,
        invoer=invoer, resultaat=resultaat, gekozen_toevoegingen=gekozen,
        huidig_jaar=date.today().isocalendar().year, offerte=offerte,
        berekening_token=token or uuid4().hex,
    ), 400 if fout else 200


def kostprijs_pagina():
    data = lezen()
    resultaat, fout = None, None
    offerte_id = request.values.get("offerte_id", "")
    offerte = zoek_offerte(data, offerte_id) if offerte_id else None
    gekozen = request.form.getlist("toevoegingen")
    invoer = request.form if request.method == "POST" else dict(request.args)
    if offerte and request.method == "GET":
        invoer.update(klant=offerte["klant"], eindklant=offerte["eindklant"])
    if request.method == "POST":
        try:
            resultaat = bereken_kostprijs(data, invoer, gekozen)
        except ValueError as exc:
            fout = str(exc)
    return toon_calculatie(data, invoer, gekozen, resultaat, fout, offerte)


@app.post("/klant-offerte/toevoegen")
def offerte_toevoegen():
    with LOCK:
        data = lezen()
        offerte_id = request.form.get("offerte_id", "")
        offerte = zoek_offerte(data, offerte_id) if offerte_id else None
        gekozen = request.form.getlist("toevoegingen")
        try:
            resultaat = bereken_kostprijs(data, request.form, gekozen)
            token = request.form.get("berekening_token", "")
            if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
                raise ValueError("Bereken het artikel opnieuw en voeg het daarna toe.")
            # Hetzelfde formulier opnieuw versturen voegt de regel niet dubbel toe.
            if offerte is None:
                offerte = next((o for o in data["klant-offertes"] if o["id"] == token), None)
            if offerte and (offerte["klant"].casefold() != resultaat["klant"].casefold() or offerte["eindklant"].casefold() != resultaat["eindklant"].casefold()):
                raise ValueError("Deze offerte is voor een andere klant/eindklant. Kies Nieuwe klant-offerte beginnen voor een andere klant.")
            if offerte is None:
                offerte = {"id": token, "projectnaam": "", "klant": resultaat["klant"],
                           "eindklant": resultaat["eindklant"], "regels": []}
                data["klant-offertes"].append(offerte)
            if not any(r["id"] == token for r in offerte["regels"]):
                offerte["regels"].append({"id": token, "berekening": resultaat})
                bewaren(data)
        except ValueError as exc:
            return toon_calculatie(data, request.form, gekozen, fout=str(exc), offerte=offerte)
        return redirect(url_for("overzicht", slug="maak-kostprijsberekening", offerte_id=offerte["id"],
                                jaar=resultaat["afleverjaar"], week=resultaat["afleverweek"], aantal=resultaat["aantal"], toegevoegd="1"))


def offerte_totalen(offerte):
    aantal, totaal, onbekend = 0, Decimal(0), 0
    for regel in offerte["regels"]:
        r = regel["berekening"]
        if not r.get("aantal"):
            onbekend += 1
            continue
        aantal += r["aantal"]
        totaal += Decimal(str(r["totaal_order"]))
    return {"aantal": aantal, "totaal": float(totaal), "onbekend": onbekend}


@app.route("/klant-offerte/<offerte_id>", methods=["GET", "POST"])
def klant_offerte(offerte_id):
    fout = None
    with LOCK:
        data = lezen()
        offerte = zoek_offerte(data, offerte_id)
        if request.method == "POST":
            naam = request.form.get("projectnaam", "").strip()
            if not naam or len(naam) > 200:
                fout = "Vul een projectnaam in van maximaal 200 tekens."
            elif not offerte["regels"]:
                fout = "Voeg eerst een artikel toe aan deze offerte."
            else:
                offerte["projectnaam"] = naam
                bewaren(data)
                return redirect(url_for("klant_offerte", offerte_id=offerte_id, bewaard="1"))
    return render_template_string(PAGINA, onderdelen=ONDERDELEN,
        onderdeel={"naam": "Klant-offerte", "tekst": "Verzamel je berekeningen en bewaar ze als project."},
        slug="klant-offerte", offerte=offerte, offertotalen=offerte_totalen(offerte), data=data, fout=fout, opgeslagen=False, invoer=request.form,
    ), 400 if fout else 200


@app.post("/klant-offerte/<offerte_id>/verwijderen/<regel_id>")
def offerte_regel_verwijderen(offerte_id, regel_id):
    with LOCK:
        data = lezen()
        offerte = zoek_offerte(data, offerte_id)
        if not any(r["id"] == regel_id for r in offerte["regels"]):
            abort(404)
        offerte["regels"] = [r for r in offerte["regels"] if r["id"] != regel_id]
        bewaren(data)
    return redirect(url_for("klant_offerte", offerte_id=offerte_id))


@app.get("/klant-offertes")
def klant_offertes():
    data = lezen()
    return render_template_string(PAGINA, onderdelen=ONDERDELEN,
        onderdeel={"naam": "Mijn projecten en offertes", "tekst": "Open een bewaard project of ga verder met een concept."},
        slug="klant-offertes", offertes=list(reversed(data["klant-offertes"])), data=data,
        fout=None, opgeslagen=False, invoer={},
    )


@app.post("/api/maak-kostprijsberekening")
def api_kostprijs():
    invoer = request.get_json(silent=True)
    if not isinstance(invoer, dict):
        return jsonify({"fout": "Stuur een JSON-object met de invoervelden."}), 400
    try:
        return jsonify(bereken_kostprijs(lezen(), invoer, invoer.get("toevoegingen", [])))
    except ValueError as exc:
        return jsonify({"fout": str(exc)}), 400


if __name__ == "__main__":
    app.run()
