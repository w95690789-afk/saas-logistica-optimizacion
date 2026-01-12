import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
import requests
import numpy as np 
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE PÁGINA Y ESTILOS ---
st.set_page_config(
    page_title="T1 LATAM | Control Tower", 
    layout="wide", 
    page_icon="🚛",
    initial_sidebar_state="expanded"
)

# --- INYECCIÓN CSS (UX/UI) ---
st.markdown("""
    <style>
    /* Importar fuente moderna */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    /* Encabezados */
    h1, h2, h3 {
        color: #0f172a; 
    }
    
    /* Botones Primarios */
    .stButton>button {
        background-color: #2563eb;
        color: white;
        border-radius: 8px;
        border: none;
        padding: 0.5rem 1rem;
        font-weight: 600;
        transition: all 0.3s ease;
        width: 100%;
    }
    .stButton>button:hover {
        background-color: #1d4ed8;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    }
    
    /* Métricas */
    div[data-testid="stMetric"] {
        background-color: #f8fafc;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
    }
    
    /* Alertas */
    .stAlert {
        border-radius: 8px;
    }
    
    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #f1f5f9;
    }
    </style>
""", unsafe_allow_html=True)

# --- ESTADO ---
if 'results_df' not in st.session_state: st.session_state['results_df'] = None
if 'api_key' not in st.session_state: st.session_state['api_key'] = ""

# ==========================================
# SECCIÓN LÓGICA (INTACTA - NO TOCAR)
# ==========================================

# --- 1. MÓDULO GOOGLE MAPS ---
def get_google_route_time(origin_lat, origin_lon, dest_lat, dest_lon, departure_time_iso, api_key):
    if not api_key: return None
    endpoint = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": api_key, "X-Goog-FieldMask": "routes.duration"}
    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lon}}},
        "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE", "departureTime": departure_time_iso 
    }
    try:
        response = requests.post(endpoint, json=body, headers=headers)
        data = response.json()
        if "routes" in data:
            seconds = int(data["routes"][0].get("duration", "0s").rstrip('s'))
            return seconds / 3600.0 
    except: pass
    return None

# --- 2. GENERADOR DE ESCENARIOS ---
def generar_escenario_pais(pais_seleccionado):
    DB_PAISES = {
        "Mexico": {
            101: {'Name': 'Planta Toluca', 'Lat': 19.28, 'Lon': -99.65, 'Type': 'Plant', 'Cap': 12},
            102: {'Name': 'Planta Monterrey', 'Lat': 25.68, 'Lon': -100.31, 'Type': 'Plant', 'Cap': 10},
            201: {'Name': 'CEDI Iztapalapa', 'Lat': 19.35, 'Lon': -99.06, 'Type': 'CEDI', 'Cap': 8},
            202: {'Name': 'CEDI Puebla', 'Lat': 19.04, 'Lon': -98.20, 'Type': 'CEDI', 'Cap': 6}
        },
        "Colombia": {
            101: {'Name': 'Planta Tocancipá', 'Lat': 4.96, 'Lon': -73.94, 'Type': 'Plant', 'Cap': 10},
            102: {'Name': 'Planta Medellín', 'Lat': 6.33, 'Lon': -75.55, 'Type': 'Plant', 'Cap': 8},
            201: {'Name': 'CEDI Bogotá Sur', 'Lat': 4.59, 'Lon': -74.15, 'Type': 'CEDI', 'Cap': 6},
            202: {'Name': 'CEDI Cali', 'Lat': 3.45, 'Lon': -76.53, 'Type': 'CEDI', 'Cap': 5}
        },
        "Peru": {101: {'Name': 'Planta Lima Ate', 'Lat': -12.02, 'Lon': -76.91, 'Type': 'Plant', 'Cap': 10}, 201: {'Name': 'CEDI Arequipa', 'Lat': -16.40, 'Lon': -71.53, 'Type': 'CEDI', 'Cap': 5}},
        "Ecuador": {101: {'Name': 'Planta Quito', 'Lat': -0.18, 'Lon': -78.46, 'Type': 'Plant', 'Cap': 8}, 201: {'Name': 'CEDI Guayaquil', 'Lat': -2.18, 'Lon': -79.88, 'Type': 'CEDI', 'Cap': 6}},
        "Chile": {101: {'Name': 'Planta Renca', 'Lat': -33.40, 'Lon': -70.70, 'Type': 'Plant', 'Cap': 10}, 201: {'Name': 'CEDI Valparaiso', 'Lat': -33.04, 'Lon': -71.61, 'Type': 'CEDI', 'Cap': 5}},
        "Argentina": {101: {'Name': 'Planta Buenos Aires', 'Lat': -34.60, 'Lon': -58.38, 'Type': 'Plant', 'Cap': 12}, 201: {'Name': 'CEDI Córdoba', 'Lat': -31.42, 'Lon': -64.18, 'Type': 'CEDI', 'Cap': 6}},
        "Brasil": {101: {'Name': 'Planta Sao Paulo', 'Lat': -23.55, 'Lon': -46.63, 'Type': 'Plant', 'Cap': 15}, 201: {'Name': 'CEDI Rio', 'Lat': -22.90, 'Lon': -43.17, 'Type': 'CEDI', 'Cap': 8}},
        "Guatemala": {101: {'Name': 'Planta Guatemala', 'Lat': 14.63, 'Lon': -90.50, 'Type': 'Plant', 'Cap': 8}, 201: {'Name': 'CEDI Quetzaltenango', 'Lat': 14.83, 'Lon': -91.51, 'Type': 'CEDI', 'Cap': 4}},
        "Panama": {101: {'Name': 'Planta Panamá', 'Lat': 9.08, 'Lon': -79.41, 'Type': 'Plant', 'Cap': 6}, 201: {'Name': 'CEDI Colón', 'Lat': 9.35, 'Lon': -79.90, 'Type': 'CEDI', 'Cap': 4}}
    }
    
    nodes = DB_PAISES.get(pais_seleccionado, DB_PAISES["Mexico"])

    def get_time_approx(lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        return max(2.0, round((R * 2 * atan2(sqrt(a), sqrt(1-a)) / 50.0) + 1, 1))

    orders = []
    keys = list(nodes.keys())
    
    for i in range(1, 101): 
        orig_id, dest_id = np.random.choice(keys), np.random.choice(keys)
        while dest_id == orig_id: dest_id = np.random.choice(keys)
        orig, dest = nodes[orig_id], nodes[dest_id]
        
        orders.append({
            'ID': f"PED-{2026000+i}", 
            'Origen_Lat': orig['Lat'], 'Origen_Lon': orig['Lon'],
            'Destino_Lat': dest['Lat'], 'Destino_Lon': dest['Lon'],
            'Tiempo_Estimado_Manual_h': get_time_approx(orig['Lat'], orig['Lon'], dest['Lat'], dest['Lon']),
            'Tiempo_Carga_h': 2.0, 'Tiempo_Descarga_h': 1.5,
            'Skill_Requerido': np.random.choice(['Seco', 'Refrigerado'], p=[0.6, 0.4]), 
            'Prioridad': 1
        })
    
    muelle_config = []
    for nid, data in nodes.items():
        is_plant = data['Type'] == 'Plant'
        for d in range(1, data['Cap'] + 1):
            if np.random.rand() < 0.5:
                ap, cl, brk = 6, 22, "12-14" 
            else:
                ap, cl, brk = (0, 24, "13-14; 21-22") if is_plant else (7, 19, "13-14")
            
            if d == 1: skill = 'Seco'
            elif d == 2: skill = 'Refrigerado'
            else:
                r = np.random.rand()
                skill = 'Mixto' if r > 0.7 else ('Refrigerado' if r > 0.5 else 'Seco')

            muelle_config.append({
                'Nodo_ID': nid, 'Nombre_Nodo': data['Name'], 'Muelle_ID': f"M{d}", 
                'Skill_Soportado': skill, 'Horario_Apertura': ap, 'Horario_Cierre': cl, 'Breaks (Inicio-Fin)': brk
            })
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as w:
        pd.DataFrame(orders).to_excel(w, sheet_name='Pedidos', index=False)
        pd.DataFrame(muelle_config).to_excel(w, sheet_name='Config_Muelles', index=False)
    return output.getvalue()

# --- 3. UTILIDADES ---
def format_time(h):
    if pd.isna(h): return "00:00"
    base = datetime.now().replace(hour=0,minute=0,second=0) + timedelta(hours=float(h))
    return f"+{int(h//24)}d {base.strftime('%H:%M')}" if h>=24 else base.strftime('%H:%M')

def parse_break_string(s):
    res = []
    if pd.isna(s) or str(s).strip() == "": return res
    for p in str(s).split(';'):
        try: res.append(tuple(map(float, p.strip().split('-'))))
        except: pass
    return res

def smart_load(f):
    try:
        xl = pd.ExcelFile(f)
        return pd.read_excel(f, 'Pedidos'), pd.read_excel(f, 'Config_Muelles')
    except: return pd.DataFrame(), pd.DataFrame()

def audit_schedule(df):
    errors = []
    for (nodo, muelle), g in df.groupby(['Nodo', 'Etiqueta Muelle']):
        g = g.sort_values('Inicio Servicio')
        last_end, last_ord = -1, ""
        for _, r in g.iterrows():
            if r['Inicio Servicio'] < last_end - 0.001: 
                errors.append({'Nodo': nodo, 'Muelle': muelle, 'Conflicto': f"{last_ord} vs {r['Orden']}"})
            last_end, last_ord = r['Fin Servicio'], r['Orden']
    return pd.DataFrame(errors)

# --- 4. MOTOR DE OPTIMIZACIÓN (LÓGICA INTACTA V15) ---
def solve_engine(df_pedidos, df_config, use_google, api_key):
    # Uso de st.status para feedback moderno en lugar de st.empty
    with st.status("🚀 Iniciando Motor de Optimización T1...", expanded=True) as status:
        
        status.write("⚙️ Configurando solver y horizonte temporal...")
        model = cp_model.CpModel()
        horizon = 120 
        
        # 1. PRE-PROCESAMIENTO
        status.write("🏗️ Construyendo infraestructura de muelles y turnos...")
        nodos_muelles = {}
        
        for _, row in df_config.iterrows():
            nid = row['Nodo_ID']
            mid = row['Muelle_ID']
            skill_m = row['Skill_Soportado']
            if nid not in nodos_muelles: nodos_muelles[nid] = []
            
            intervals_bloqueados = []
            op, cl = row.get('Horario_Apertura', 0), row.get('Horario_Cierre', 24)
            breaks_list = parse_break_string(row.get('Breaks (Inicio-Fin)', ''))
            
            for day in range(5): 
                off = day * 24
                if op > 0: intervals_bloqueados.append((0+off, op))
                if cl < 24: intervals_bloqueados.append((cl+off, 24-cl))
                for s, e in breaks_list:
                    dur = e - s
                    if dur > 0: intervals_bloqueados.append((s+off, dur))
            
            cp_intervals_bloqueados = []
            for start, dur in intervals_bloqueados:
                iv = model.NewIntervalVar(int(start), int(dur), int(start+dur), f"bk_{mid}_{start}")
                cp_intervals_bloqueados.append(iv)
                
            nodos_muelles[nid].append({
                'id': mid, 'skill': skill_m, 'nombre_nodo': row['Nombre_Nodo'],
                'bloqueos': cp_intervals_bloqueados, 'ordenes_asignadas': []
            })

        pedidos_vars = []
        route_cache = {}
        
        status.write("📦 Procesando pedidos y calculando rutas (Google/Manual)...")
        # Procesamiento de pedidos (sin barra de progreso visual para no ensuciar el status)
        for i, row in df_pedidos.iterrows():
            pid, skill_req = row['ID'], row['Skill_Requerido']
            
            tv = row.get('Tiempo_Estimado_Manual_h', 5)
            if use_google and api_key and pd.notna(row.get('Origen_Lat')):
                k = (row['Origen_Lat'], row['Origen_Lon'], row['Destino_Lat'], row['Destino_Lon'])
                if k not in route_cache:
                    dt = (datetime.utcnow()+timedelta(days=1)).replace(hour=8).isoformat()+'Z'
                    t_g = get_google_route_time(*k, dt, api_key)
                    if t_g: route_cache[k] = t_g
                tv = route
