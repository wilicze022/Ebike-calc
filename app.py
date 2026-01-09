import streamlit as st
import pandas as pd
import math

# =========================
# KONSTANTY / PARAMETRY
# =========================
CDA = 0.45        # Aerodynamický odpor (Cd*A) [m^2]
CRR = 0.008       # Valivý odpor [-]
RHO = 1.225       # Hustota vzduchu [kg/m^3]
G_GRAV = 9.81     # Gravitace [m/s^2]

EFF_MOTOR = 0.85  # Účinnost motoru (el. -> mech.) [-]
G_STC = 1000.0    # STC ozáření pro Wp rating [W/m^2]
system_eff = 0.5
# =========================
# POMOCNÉ FUNKCE
# =========================
def format_hours_minutes(hours_float: float) -> str:
    """Convert hours (float) -> 'X h Y min' (rounded to nearest minute)."""
    if hours_float is None or hours_float <= 0:
        return "0 h 0 min"
    total_minutes = int(round(hours_float * 60))
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h} h {m} min"

def battery_temp_factor(temp_c: float) -> float:
    """
    Konzervativní korekce využitelné kapacity Li-ion baterie.
    Modeluje okamžitý vliv teploty na dostupnou energii (ne dlouhodobou degradaci).

    25 °C -> 1.00
    0 °C  -> ~0.85
    -10 °C -> ~0.65 (min 0.65)
    Nad 25 °C kapacitu nezvyšujeme (konzervativní).
    """
    if temp_c >= 25:
        return 1.0
    elif temp_c >= 0:
        return 1.0 - (25 - temp_c) * 0.006  # 0°C => 0.85
    else:
        return max(0.65, 0.85 + temp_c * 0.02)  # -10°C => 0.65

# =========================
# STREAMLIT SETUP
# =========================
st.set_page_config(page_title="Solar Bike Thesis", layout="wide")

# =========================
# DATABÁZE LOKALIT
# =========================
# G = průměrné ozáření během jízdy [W/m^2] (zjednodušený průměr pro model)
LOKALITA_DATA = {
    # --- Kladno + okolí ---
    "Kladno (centrum – město)":              {"sklon": 1.2, "G": 650},
    "Kladno (Dubí / lesní okraj)":           {"sklon": 1.8, "G": 640},
    "Buštěhrad":                             {"sklon": 1.0, "G": 650},
    "Unhošť":                                {"sklon": 1.4, "G": 660},
    "Slaný":                                 {"sklon": 1.3, "G": 655},
    "Stochov + Lány":                        {"sklon": 2.2, "G": 635},
    "Lidice / Makotřasy":                    {"sklon": 1.1, "G": 665},

    # --- Praha + okolí ---
    "Praha (centrum)":                       {"sklon": 1.8, "G": 640},
    "Praha (západ)":                         {"sklon": 2.2, "G": 645},
    "Praha (východ)":                        {"sklon": 1.6, "G": 645},
    "Beroun":                                {"sklon": 2.8, "G": 650},
    "Karlštejn":                             {"sklon": 3.5, "G": 630},
    "Kralupy n. Vltavou":                    {"sklon": 1.7, "G": 645},
    "Mělník":                                {"sklon": 1.9, "G": 660},
    "Rakovník":                              {"sklon": 2.1, "G": 650},

    # --- mimo Středočeský kraj, ale pořád CZ ---
    "Plzeň":                                 {"sklon": 1.9, "G": 660},
    "České Budějovice":                      {"sklon": 1.5, "G": 680},
    "Hradec Králové":                        {"sklon": 0.9, "G": 660},
    "Brno":                                  {"sklon": 2.4, "G": 700},
    "Ostrava":                               {"sklon": 1.6, "G": 630},

    # Vlastní
    "Vlastní nastavení":                     {"sklon": 0.0, "G": 650},
}

# =========================
# SIDEBAR – VSTUPY
# =========================
st.sidebar.header("⚙️ 1) Parametry jízdy")
hmotnost = st.sidebar.number_input(
    "Celková hmotnost (kg)", value=100, min_value=30, max_value=250,
    help="Součet jezdce + kola + nákladu."
)

rychlost_kmh = st.sidebar.number_input(
    "Prům. rychlost (km/h)", value=25, min_value=1, max_value=60,
    help="Model počítá ustálenou jízdu konstantní rychlostí."
)

vykon_jezdec = st.sidebar.number_input(
    "Výkon jezdce (mechanický) [W]", value=120, min_value=0, max_value=400,
    help="Průměrný výkon, který dodává jezdec šlapáním. Typicky 80–150 W."
)

teplota_c = st.sidebar.slider(
    "Venkovní teplota (°C)", -20, 40, 20,
    help="Ovlivňuje využitelnou kapacitu Li-ion baterie (v chladu klesá dostupná energie)."
)

st.sidebar.header("🔋 2) Baterie a Motor")
kapacita_wh = st.sidebar.number_input(
    "Kapacita baterie [Wh]", value=540, min_value=50, max_value=5000,
    help="Nominální energie baterie (např. 36V 15Ah ≈ 540Wh)."
)

asistence_proc = st.sidebar.slider(
    "Asistence motoru (%)", 0, 100, 100,
    help="V tomto modelu nastavuje, jakou část nominálního odběru motor skutečně bere."
)

# IMPORTANT: v tomto modelu je to *elektrický příkon motoru při 100% asistenci*
vykon_motoru_nom = st.sidebar.number_input(
    "Motor – elektrický odběr při 100% asistenci [W]",
    value=250, min_value=0, max_value=2000,
    help=(
        "Elektrický příkon motoru z baterie při 100% asistenci. "
        "Např. 250 W při 25 km/h odpovídá spotřebě ~10 Wh/km."
    )
)
uhel_stupne = st.sidebar.slider(
    "Efektivní úhel dopadu slunečního záření (°)",
    0, 90, 30,
    help=(
        "Úhel mezi směrem slunečních paprsků a kolmicí (normálou) k panelu. "
        "Výkon panelu je úměrný cos(θ): 0° maximum, 60° ~ polovina, 90° ~ 0. "
        "Jedná se o efektivní (průměrnou) hodnotu během jízdy."
    )
)
st.sidebar.caption("Příklady: 20–30° velmi dobré • 30–45° běžné • 60° špatné • 90° žádný výkon")
st.sidebar.header("☀️ 3) Solár a Lokalita")
vykon_panelu_wp = st.sidebar.number_input(
    "Nominální výkon panelu [Wp]", value=100, min_value=0, max_value=1000,
    help="Výkon v STC podmínkách (1000 W/m², 25 °C, ideální orientace)."
)

lokalita = st.sidebar.selectbox(
    "Vyberte lokalitu jízdy", list(LOKALITA_DATA.keys())
)

# Lokalita -> sklon + ozáření G
if lokalita == "Vlastní nastavení":
    sklon_proc = st.sidebar.slider("Terén – sklon [%]", -5.0, 15.0, 0.0)
    solar_G = st.sidebar.number_input(
        "Solární ozáření G [W/m²]", value=700, min_value=0, max_value=1100,
        help="Fyzikální solar irradiance. Typicky 100–1000 W/m² během dne."
    )
else:
    sklon_proc = float(LOKALITA_DATA[lokalita]["sklon"])
    solar_G = float(LOKALITA_DATA[lokalita]["G"])






# =========================
# VÝPOČTY – SOLÁR
# =========================
cos_theta = max(math.cos(math.radians(uhel_stupne)), 0.0)

# P_solar = Wp * (G/1000) * cos(theta) * system_eff
P_solar_w = vykon_panelu_wp * (solar_G / G_STC) * cos_theta * system_eff

st.sidebar.info(f"📍 **{lokalita}**")
st.sidebar.write(f"Sklon: **{sklon_proc:.1f}%**")
st.sidebar.write(f"Ozáření G: **{solar_G:.0f} W/m²**")
st.sidebar.write(f"cos(θ): **{cos_theta:.2f}**")
st.sidebar.success(f"☀️ Solární výkon během jízdy: **{P_solar_w:.1f} W**")

# =========================
# VÝPOČTY – FYZIKA JÍZDY
# =========================
v_ms = rychlost_kmh / 3.6

F_air = 0.5 * RHO * (v_ms ** 2) * CDA
F_roll = hmotnost * G_GRAV * CRR
F_slope = hmotnost * G_GRAV * math.sin(math.atan(sklon_proc / 100.0))
F_total = max(F_air + F_roll + F_slope, 0.0)

P_mech_required = F_total * v_ms  # W (potřebný mech. výkon)

# Motor odběr dle asistence (elektrický)
P_motor_elec = vykon_motoru_nom * (asistence_proc / 100.0)  # W (z baterie)
P_motor_mech = P_motor_elec * EFF_MOTOR                      # W (na kole)

P_rider = vykon_jezdec
P_mech_total_available = P_rider + P_motor_mech

# Feasibility: zvládne jezdec+motor držet rychlost?
# (Když je asistence 0%, testujeme jen jezdce.)
if asistence_proc > 0:
    can_hold_speed = P_mech_total_available >= P_mech_required
else:
    can_hold_speed = P_rider >= P_mech_required

# =========================
# VÝPOČTY – TEPLOTA BATERIE
# =========================
temp_factor = battery_temp_factor(teplota_c)
kapacita_wh_eff = kapacita_wh * temp_factor

# =========================
# VÝPOČTY – MAX DOJEZD
# =========================
if P_motor_elec <= 0:
    # 0% asistence => motor nebere energii z baterie
    spotreba_wh_km = 0.0
    spotreba_wh_km_solar = 0.0
    dojezd_bat = 0.0
    dojezd_solar = 0.0
    bonus_km = 0.0
    doba_jizdy_h = 0.0
    dodana_energie_wh = 0.0
else:
    spotreba_wh_km = P_motor_elec / rychlost_kmh  # Wh/km

    dojezd_bat = kapacita_wh_eff / spotreba_wh_km if spotreba_wh_km > 0 else 0.0

    P_bat_net = max(P_motor_elec - P_solar_w, 1e-6)  # W
    spotreba_wh_km_solar = P_bat_net / rychlost_kmh

    dojezd_solar = kapacita_wh_eff / spotreba_wh_km_solar if spotreba_wh_km_solar > 0 else 0.0
    bonus_km = dojezd_solar - dojezd_bat

    doba_jizdy_h = dojezd_solar / rychlost_kmh
    dodana_energie_wh = P_solar_w * doba_jizdy_h

# =========================
# UI – VÝSLEDKY (MAX DOJEZD)
# =========================
st.title("🔋 Solar Bike Thesis: Kalkulátor")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Spotřeba motoru", f"{spotreba_wh_km:.1f} Wh/km" if spotreba_wh_km > 0 else "—")
m2.metric("Dojezd (jen baterie)", f"{dojezd_bat:.1f} km" if dojezd_bat > 0 else "—")

delta_text = f"+{bonus_km:.1f} km" if (dojezd_bat > 0 and dojezd_solar > 0) else None
m3.metric("Dojezd (baterie + solár)", f"{dojezd_solar:.1f} km" if dojezd_solar > 0 else "—", delta=delta_text)

m4.metric("Solární výkon", f"{P_solar_w:.1f} W")

if asistence_proc > 0 and not can_hold_speed:
    st.warning(
        f"⚠️ Zvolenou rychlost pravděpodobně neudržíš: potřebný výkon ~{P_mech_required:.0f} W (mech.), "
        f"ale jezdec+motor dodají ~{P_mech_total_available:.0f} W (mech.). "
        f"Zkus snížit rychlost / sklon nebo zvýšit výkon jezdce/asistenci."
    )

if P_motor_elec <= 0:
    st.info("ℹ️ Asistence je 0% → motor nebere energii z baterie, dojezd na baterii zde nedává smysl.")

st.divider()

col_graph, col_data = st.columns([2, 1])

with col_graph:
    st.subheader("Porovnání max dojezdu")
    chart_data = pd.DataFrame({
        "Zdroj": ["Jen Baterie", "Baterie + Solár"],
        "Dojezd (km)": [dojezd_bat, dojezd_solar]
    })
    st.bar_chart(chart_data.set_index("Zdroj"))


# =========================
# ANALÝZA KONKRÉTNÍ TRASY (DoD & efektivita soláru)
# =========================
st.subheader("📏 Analýza konkrétní trasy (DoD & efektivita soláru)")

trip_km = st.number_input(
    "Délka trasy (km)",
    value=20.0,
    min_value=0.0,
    step=1.0,
    help="Zadej délku plánované jízdy. Spočítáme DoD baterie a kolik energie pokryje solár."
)

if rychlost_kmh > 0 and trip_km > 0:
    trip_time_h = trip_km / rychlost_kmh

    # Energie motoru a soláru během TRIPU
    E_motor_wh = P_motor_elec * trip_time_h
    E_solar_wh = P_solar_w * trip_time_h

    # Netto energie z baterie (solár snižuje odběr)
    E_batt_wh = max(P_motor_elec - P_solar_w, 0.0) * trip_time_h

    dod_trip = (E_batt_wh / kapacita_wh_eff) if kapacita_wh_eff > 0 else 0.0
    dod_trip_pct = dod_trip * 100.0

    solar_share = (E_solar_wh / E_motor_wh) if E_motor_wh > 0 else 0.0
    solar_share_pct = min(solar_share * 100.0, 100.0)

    remaining_wh = max(kapacita_wh_eff - E_batt_wh, 0.0)
    remaining_pct = (remaining_wh / kapacita_wh_eff * 100.0) if kapacita_wh_eff > 0 else 0.0

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Čas jízdy", format_hours_minutes(trip_time_h))
    a2.metric("DoD (vybití baterie)", f"{dod_trip_pct:.1f} %")
    a3.metric("Solár pokryl z motoru", f"{solar_share_pct:.1f} %")
    a4.metric("Zbývá v baterii", f"{remaining_wh:.0f} Wh", delta=f"{remaining_pct:.1f} %")

    # Stavové hlášky
    if P_motor_elec <= 0:
        st.info("ℹ️ Motor má odběr 0 W (asistence 0%). DoD pro motor je 0% a solár nehraje roli v bilanci motoru.")
    else:
        if E_batt_wh > kapacita_wh_eff:
            st.error(
                f"❌ Na tuto trasu nestačí baterie: potřebuješ ~{E_batt_wh:.0f} Wh z baterie, "
                f"ale máš jen ~{kapacita_wh_eff:.0f} Wh využitelných."
            )
        elif dod_trip_pct > 80:
            st.warning("⚠️ DoD je nad 80% – to je velké vybití (horší pro životnost).")

    # Volitelné detailní hodnoty (přehledné pro thesis)
    with st.expander("Zobrazit detaily výpočtu (Wh)"):
        st.write(f"Motor energie (trip): **{E_motor_wh:.0f} Wh**")
        st.write(f"Solár energie (trip): **{E_solar_wh:.0f} Wh**")
        st.write(f"Baterie energie (trip, netto): **{E_batt_wh:.0f} Wh**")
        st.write(f"Využitelná kapacita baterie: **{kapacita_wh_eff:.0f} Wh**")

else:
    st.caption("Zadej délku trasy > 0 km (a rychlost > 0), aby se spočítala DoD a efektivita soláru.")
