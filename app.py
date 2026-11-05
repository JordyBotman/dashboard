from flask import Flask, render_template_string, url_for, abort, request, redirect, jsonify
from pathlib import Path
from decimal import Decimal, InvalidOperation
from threading import RLock
from uuid import uuid4
import json
import os
import tempfile

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


def standaard_substraten():
    return [{"id": "substraat-" + str(i), "naam": naam, "prijs_per_liter": prijs}
            for i, (naam, prijs) in enumerate([
                ("Violengrond", 10), ("Perkgoedgrond", 15),
                ("Bolchrysantengrond", 20), ("Kerststerrengrond", 25)], 1)]


def standaard_schemas():
    return [{"id": "schema-" + str(i), "naam": naam,
             "fasen": {fase: {"weken": i + 4, "planten_per_m2": i * 100}
                       for fase in FASEN}}
            for i, naam in enumerate(VERKOOP, 1)]


def begindata():
    return {
        "verkoopartikelen": [{"id": str(i), "naam": n} for i, n in enumerate(VERKOOP, 1)],
        "opkweekverpakkingen": [{"id": str(i), "naam": n, "prijs_per_1000": prijs, "inhoud_liter": None}
                            for i, (n, prijs) in enumerate(zip(INKOOP, [90, 100, 120, 130, 170, 190]), 1)],
        "productieschemas": standaard_schemas(),
        "schemas_startversie": 1,
        "substraten": standaard_substraten(),
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
        with DATA_FILE.open(encoding="utf-8") as f:
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
        gewijzigd = False
        if "opkweekverpakkingen" not in data and "inkoopartikelen" in data:
            data["opkweekverpakkingen"] = data.pop("inkoopartikelen")
            gewijzigd = True
        for artikel in data["opkweekverpakkingen"]:
            if "inhoud_liter" not in artikel:
                artikel["inhoud_liter"] = None
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


def inhoud_invoer():
    waarde = request.form.get("inhoud_liter", "").strip()
    return getal(waarde, "inhoud in liters grond") if waarde else None


def naam_invoer():
    naam = request.form.get("naam", "").strip()
    if not naam or len(naam) > 200:
        raise ValueError("Vul een naam in van maximaal 200 tekens.")
    return naam


def verrijk(data, slug):
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
  </style>
</head>
<body class="{{ 'schema-pagina' if slug == 'productieschemas' else '' }}">
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
          <div class="tabel"><table><thead><tr><th>Artikel</th>{% if slug == 'opkweekverpakkingen' %}<th>Prijs per 1000 (€)</th><th>Prijs per stuk</th><th>Inhoud grond (liter/artikel)</th><th>Actie</th>{% endif %}</tr></thead>
          <tbody>{% for a in rijen %}<tr><td>{{ a.naam }}</td>
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
        <a class="api" href="{{ url_for('api_overzicht', slug=slug) }}" target="_blank" rel="noopener">Bekijk JSON ↗</a>
        <a class="terug" href="{{ url_for('start') }}">← Terug naar de startpagina</a>
      </section>
    {% else %}
      <div class="sectiekop"><h2>Waar wil je naartoe?</h2><span class="hint">Vijf onderdelen. Eén overzicht.</span></div>
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
    <footer><span>Kwekerij Wouters!!</span><span>Assortiment · Inkoop · Productie</span></footer>
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
</script>
</body>
</html>
"""


@app.route("/")
def start():
    return render_template_string(PAGINA, onderdelen=ONDERDELEN, onderdeel=None)


@app.route("/<slug>", methods=["GET", "POST"])
def overzicht(slug):
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
                if slug in ("verkoopartikelen", "opkweekverpakkingen"):
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
    response = jsonify(verrijk(lezen(), slug))
    response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    app.run()
