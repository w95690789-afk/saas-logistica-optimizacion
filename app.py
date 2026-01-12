import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
import requests
import numpy as np 
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SaaS Logístico LATAM", layout="wide", page_icon="🌎")

if 'results_df' not in st.session_state: st.session_state['results_df'] = None
if 'api_key' not in st.session_state: st.session_state['api_key'] = ""

# --- BASE DE DATOS GEOGRÁFICA (LATAM) ---
DB_PAISES = {
    "Mexico": {
        101: {'Name': 'Planta Toluca Central', 'Lat': 19.2826, 'Lon': -99.6557, 'Type': 'Plant', 'Cap': 12},
        102: {'Name': 'Planta Monterrey Norte', 'Lat': 25.6866, 'Lon': -100.3161, 'Type': 'Plant', 'Cap': 10},
        103: {'Name': 'Planta Guadalajara Occidente', 'Lat': 20.6597, 'Lon': -103.3496, 'Type': 'Plant', 'Cap': 10},
        201: {'Name': 'CEDI CDMX Iztapalapa', 'Lat': 19.3552, 'Lon': -99.0622, 'Type': 'CEDI', 'Cap': 8},
        202: {'Name': 'CEDI Puebla', 'Lat': 19.0414, 'Lon': -98.2063, 'Type': 'CEDI', 'Cap': 6},
        203: {'Name': 'CEDI Veracruz Puerto', 'Lat': 19.1738, 'Lon': -96.1342, 'Type': 'CEDI', 'Cap': 5},
        204: {'Name': 'CEDI Tijuana Frontera', 'Lat': 32.5149, 'Lon': -117.0382, 'Type': 'CEDI', 'Cap': 5},
    },
    "Colombia": {
        101: {'Name': 'Planta Tocancipá (Bogotá)', 'Lat': 4.9654, 'Lon': -73.9429, 'Type': 'Plant', 'Cap': 10},
        102: {'Name': 'Planta Medellín (Bello)', 'Lat': 6.3373, 'Lon': -75.5579, 'Type': 'Plant', 'Cap': 8},
        201: {'Name': 'CEDI Bogotá Sur', 'Lat': 4.5981, 'Lon': -74.1558, 'Type': 'CEDI', 'Cap': 6},
        202: {'Name': 'CEDI Cali-Yumbo', 'Lat': 3.5264, 'Lon': -76.5057, 'Type': 'CEDI', 'Cap': 6},
        203: {'Name': 'CEDI Barranquilla', 'Lat': 10.9685, 'Lon': -74.7813, 'Type': 'CEDI', 'Cap': 5},
        204: {'Name': 'CEDI Bucaramanga', 'Lat': 7.1193, 'Lon': -73.1227, 'Type': 'CEDI', 'Cap': 4},
        205: {'Name': 'CEDI Pereira', 'Lat': 4.8133, 'Lon': -75.6961, 'Type': 'CEDI', 'Cap': 4},
    },
    "Brasil": {
        101: {'Name': 'Planta São Paulo (Jundiaí)', 'Lat': -23.1857, 'Lon': -46.8978, 'Type': 'Plant', 'Cap': 15},
        102: {'Name': 'Planta Rio de Janeiro', 'Lat': -22.9068, 'Lon': -43.1729, 'Type': 'Plant', 'Cap': 10},
        201: {'Name': 'CEDI Belo Horizonte', 'Lat': -19.9167, 'Lon': -43.9345, 'Type': 'CEDI', 'Cap': 8},
        202: {'Name': 'CEDI Curitiba', 'Lat': -25.4284, 'Lon': -49.2733, 'Type': 'CEDI', 'Cap': 6},
        203: {'Name': 'CEDI Porto Alegre', 'Lat': -30.0346, 'Lon': -51.2177, 'Type': 'CEDI', 'Cap': 6},
        204: {'Name': 'CEDI Salvador', 'Lat': -12.9777, 'Lon': -38.5016, 'Type': 'CEDI', 'Cap': 5},
    },
    "Argentina": {
        101: {'Name': 'Planta Buenos Aires (Central)', 'Lat': -34.6037, 'Lon': -58.3816, 'Type': 'Plant', 'Cap': 12},
        102: {'Name': 'Planta Córdoba', 'Lat': -31.4201, 'Lon': -64.1888, 'Type': 'Plant', 'Cap': 8},
        201: {'Name': 'CEDI Rosario', 'Lat': -32.9442, 'Lon': -60.6505, 'Type': 'CEDI', 'Cap': 6},
        202: {'Name': 'CEDI Mendoza', 'Lat': -32.8895, 'Lon': -68.8458, 'Type': 'CEDI', 'Cap': 5},
        203: {'Name': 'CEDI Mar del Plata', 'Lat': -38.0055, 'Lon': -57.5426, 'Type': 'CEDI', 'Cap': 4},
    },
    "Chile": {
        101: {'Name': 'Planta Santiago (Renca)', 'Lat': -33.4057, 'Lon': -70.7042, 'Type': 'Plant', 'Cap': 10},
        201: {'Name': 'CEDI Valparaíso', 'Lat': -33.0472, 'Lon': -71.6127, 'Type': 'CEDI', 'Cap': 6},
        202: {'Name': 'CEDI Concepción', 'Lat': -36.8201, 'Lon': -73.0444, 'Type': 'CEDI', 'Cap': 5},
        203: {'Name': 'CEDI Antofagasta', 'Lat': -23.6509, 'Lon': -70.3975, 'Type': 'CEDI', 'Cap': 4},
    },
    "Peru": {
        101: {'Name': 'Planta Lima (Ate)', 'Lat': -12.0258, 'Lon': -76.9189, 'Type': 'Plant', 'Cap': 10},
        201: {'Name': 'CEDI Arequipa', 'Lat': -16.4090, 'Lon': -71.5375, 'Type': 'CEDI', 'Cap': 6},
        202: {'Name': 'CEDI Trujillo', 'Lat': -8.1160, 'Lon': -79.0300, 'Type': 'CEDI', 'Cap': 5},
        203: {'Name': 'CEDI Cusco', 'Lat': -13.5320, 'Lon': -71.9675, 'Type': 'CEDI', 'Cap': 4},
    },
    "Ecuador": {
        101: {'Name': 'Planta Quito (Machachi)', 'Lat': -0.5086, 'Lon': -78.5675, 'Type': 'Plant', 'Cap': 8},
        201: {'Name': 'CEDI Guayaquil', 'Lat': -2.1894, 'Lon': -79.8891, 'Type': 'CEDI', 'Cap': 6},
        202: {'Name': 'CEDI Cuenca', 'Lat': -2.9001, 'Lon': -79.0059, 'Type': 'CEDI', 'Cap': 4},
    },
    "Panama": {
        101: {'Name': 'Planta Ciudad de Panamá', 'Lat': 9.0820, 'Lon': -79.4125, 'Type': 'Plant', 'Cap': 8},
        201: {'Name': 'CEDI Colón', 'Lat': 9.3598, 'Lon': -79.9015, 'Type': 'CEDI', 'Cap': 5},
        202: {'Name': 'CEDI David', 'Lat': 8.4273, 'Lon': -82.4309, 'Type': 'CEDI', 'Cap': 4},
    },
    "Guatemala": {
        101: {'Name': 'Planta Ciudad de Guatemala', 'Lat': 14.6349, 'Lon': -90.5069, 'Type': 'Plant', 'Cap': 10},
        201: {'Name': 'CEDI Quetzaltenango', 'Lat': 14.8347, 'Lon': -91.5181, 'Type': 'CEDI', 'Cap': 5},
        202: {'Name': 'CEDI Escuintla', 'Lat': 14.3050, 'Lon': -90.7850, 'Type': 'CEDI', 'Cap': 5},
    }
}

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

# --- 2. GENERADOR DE ESCENARIOS MULTI-PAÍS ---
def generar_escenario_pais(pais_seleccionado):
    nodes = DB_PAISES.get(pais_seleccionado, DB_PAISES["Mexico"]) # Default Mexico

    def get_time(lat1, lon1, lat2, lon2): # Haversine
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        return max(2.0, round((R * 2 * atan2(sqrt(a), sqrt(1-a)) / 60.0) + 1, 1))

    orders = []
    # Convertir dict a listas
    keys = list(nodes.keys())
    plant_ids = [k for k,v in nodes.items() if v['Type']=='Plant']
    cedi_ids = [k for k,v in nodes.items() if v['Type']=='CEDI']
    
    # Generar 100 Pedidos
    for i in range(1, 101):
        if np.random.rand() < 0.8: # 80% Planta -> CEDI
            orig_id = np.random.choice(plant_ids)
            dest_id = np.random.choice(cedi_ids)
        else: # 20% Inter-CEDI
            orig_id = np.random.choice(cedi_ids)
            dest_id = np.random.choice(cedi_ids)
            while dest_id == orig_id: dest_id = np.random.choice(cedi_ids) # Evitar bucles
            
        orig, dest = nodes[orig_id], nodes[dest_id]
        
        orders.append({
            'ID': f"PED-{pais_seleccionado[:3].upper()}-{2026000+i}", 
            'Origen_Lat': orig['Lat'], 'Origen_Lon': orig['Lon'],
            'Destino_Lat': dest['Lat'], 'Destino_Lon': dest['Lon'],
            'Tiempo_Estimado_Manual_h': get_time(orig['Lat'], orig['Lon'], dest['Lat'], dest['Lon']),
            'Tiempo_Carga_h': 2, 'Tiempo_Descarga_h': 1.5,
            'Skill_Requerido': np.random.choice(['Seco', 'Refrigerado'], p=[0.7, 0.3]), 
            'Prioridad': np.random.randint(1, 5)
        })
    
    muelle_config = []
    for nid, data in nodes.items():
        for d in range(1, data['Cap'] + 1):
            skill = 'Refrigerado' if d > data['Cap']-2 else 'Seco'
            # Plantas abren 24h, CEDIs turno diurno
            apertura = 0 if data['Type'] == 'Plant' else 6
            cierre = 24 if data['Type'] == 'Plant' else 22
            
            muelle_config.append({
                'Nodo_ID': nid, 'Nombre_Nodo': data['Name'], 'Muelle_ID': f"M{d}", 
                'Skill_Soportado': skill, 'Horario_Apertura': apertura, 'Horario_Cierre': cierre, 'Breaks (Inicio-Fin)': "13-14"
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
    if pd.isna(s): return res
    for p in str(s).split(';'):
        try: res.append(tuple(map(float, p.split('-'))))
        except: pass
    return res

def smart_load(f):
    try:
        xl = pd.ExcelFile(f)
        return pd.read_excel(f, 'Pedidos'), pd.read_excel(f, 'Config_Muelles')
    except: return pd.DataFrame(), pd.DataFrame()

# --- 4. ALGORITMO DE ASIGNACIÓN REAL ---
def asignar_muelles_reales(df_resultados, df_config):
    df_final = df_resultados.copy()
    df_final['Etiqueta Muelle'] = "Sin Asignar"
    for (nodo, skill), grupo in df_final.groupby(['Nodo', 'Skill']):
        grupo = grupo.sort_values('Inicio Servicio')
        muelles_disponibles = df_config[(df_config['Nombre_Nodo'] == nodo) & (df_config['Skill_Soportado'] == skill)]['Muelle_ID'].unique()
        if len(muelles_disponibles) == 0: muelles_disponibles = [f"Virtual_{skill}_{i+1}" for i in range(5)]
        docks_status = {m: 0 for m in muelles_disponibles}
        for idx, row in grupo.iterrows():
            start, end = row['Inicio Servicio'], row['Fin Servicio']
            assigned = None
            for m_id in muelles_disponibles:
                if start >= docks_status[m_id]:
                    docks_status[m_id] = end
                    assigned = m_id
                    break
            if not assigned:
                best_m = min(docks_status, key=docks_status.get)
                docks_status[best_m] = end 
                assigned = best_m
            df_final.at[idx, 'Etiqueta Muelle'] = str(assigned)
    return df_final

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

# --- 5. MOTOR DE OPTIMIZACIÓN ---
def solve_engine(df_pedidos, df_config, use_google, api_key):
    status_ph = st.empty()
    status_ph.info("⚙️ Optimizando Red Logística...")
    
    model = cp_model.CpModel()
    horizon = 96
    
    resource_map = {} 
    dock_caps = df_config.groupby(['Nodo_ID', 'Skill_Soportado']).size().to_dict()
    for k in dock_caps: resource_map[k] = []

    pedidos_vars = []
    route_cache = {}
    
    prog = st.progress(0)
    for i, row in df_pedidos.iterrows():
        prog.progress((i+1)/len(df_pedidos))
        pid, skill = row['ID'], row['Skill_Requerido']
        
        tv = row.get('Tiempo_Estimado_Manual_h', 5)
        if use_google and api_key and pd.notna(row.get('Origen_Lat')):
            k = (row['Origen_Lat'], row['Origen_Lon'], row['Destino_Lat'], row['Destino_Lon'])
            if k not in route_cache:
                dt = (datetime.utcnow()+timedelta(days=1)).replace(hour=8).isoformat()+'Z'
                t_g = get_google_route_time(*k, dt, api_key)
                if t_g: route_cache[k] = t_g
            tv = route_cache.get(k, tv)
            
        tc, td = int(row.get('Tiempo_Carga_h', 2)), int(row.get('Tiempo_Descarga_h', 2))
        tv = int(tv)
        so, eo = model.NewIntVar(0, horizon, f'so_{pid}'), model.NewIntVar(0, horizon, f'eo_{pid}')
        ivo = model.NewIntervalVar(so, tc, eo, f'ivo_{pid}')
        sd, ed = model.NewIntVar(0, horizon, f'sd_{pid}'), model.NewIntVar(0, horizon, f'ed_{pid}')
        ivd = model.NewIntervalVar(sd, td, ed, f'ivd_{pid}')
        model.Add(sd >= eo + tv)
        
        # Asignación de Nodos (Heurística de Match por Latitud aproximada para simulación)
        # En la simulación usamos la latitud exacta para encontrar el ID del nodo en config
        # Para ser robustos, buscaremos el Nodo_ID que tenga Lat/Lon idéntico en DB_PAISES (no disponible aquí directamente)
        # MEJOR: Usaremos el pool de skills.
        try:
            cand_nodes = df_config[df_config['Skill_Soportado'] == skill]['Nodo_ID'].unique()
            if len(cand_nodes) > 0:
                n_orig = cand_nodes[i % len(cand_nodes)]
                n_dest = cand_nodes[(i+1) % len(cand_nodes)]
            else: continue
        except: continue
        
        if (n_orig, skill) in resource_map: resource_map[(n_orig, skill)].append(ivo)
        if (n_dest, skill) in resource_map: resource_map[(n_dest, skill)].append(ivd)
        pedidos_vars.append({'id': pid, 'vars': (so,eo,sd,ed), 'skill': skill, 'no': n_orig, 'nd': n_dest})

    for (nid, skill), ivs in resource_map.items():
        if not ivs: continue
        cap = dock_caps.get((nid, skill), 1)
        muelles_cf = df_config[(df_config['Nodo_ID']==nid) & (df_config['Skill_Soportado']==skill)]
        breaks_ivs = []
        for _, r in muelles_cf.iterrows():
            for d in range(4):
                off = d*24
                op, cl = r.get('Horario_Apertura', 0), r.get('Horario_Cierre', 24)
                if op > 0: breaks_ivs.append(model.NewIntervalVar(0+off, op, 0+off+op, 'c_am'))
                if cl < 24: breaks_ivs.append(model.NewIntervalVar(cl+off, 24-cl, 24+off, 'c_pm'))
                for s, e in parse_break_string(r.get('Breaks (Inicio-Fin)', '')):
                    if (e-s)>0: breaks_ivs.append(model.NewIntervalVar(int(s+off), int(e-s), int(e+off), 'bk'))
        model.AddCumulative(ivs + breaks_ivs, [1]*len(ivs + breaks_ivs), cap)

    obj = model.NewIntVar(0, horizon, 'mk')
    if pedidos_vars: 
        model.AddMaxEquality(obj, [p['vars'][3] for p in pedidos_vars])
        model.Minimize(obj)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 45
    st_solve = solver.Solve(model)
    
    if st_solve in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        status_ph.success("✅ Solución Encontrada")
        res = []
        for p in pedidos_vars:
            v = p['vars']
            so, eo, sd, ed = solver.Value(v[0]), solver.Value(v[1]), solver.Value(v[2]), solver.Value(v[3])
            n_orig_name = df_config[df_config['Nodo_ID']==p['no']]['Nombre_Nodo'].iloc[0]
            n_dest_name = df_config[df_config['Nodo_ID']==p['nd']]['Nombre_Nodo'].iloc[0]
            res.append({'Orden': p['id'], 'Tipo': 'Origen', 'Nodo': n_orig_name, 'Skill': p['skill'], 'Inicio Servicio': so, 'Fin Servicio': eo})
            res.append({'Orden': p['id'], 'Tipo': 'Destino', 'Nodo': n_dest_name, 'Skill': p['skill'], 'Inicio Servicio': sd, 'Fin Servicio': ed})
        return asignar_muelles_reales(pd.DataFrame(res), df_config)
    else:
        status_ph.error("⚠️ No se pudo optimizar (Revise capacidades/skills).")
        return pd.DataFrame()

# --- INTERFAZ UI ---
with st.sidebar:
    st.header("🔧 Configuración")
    api_k = st.text_input("Google API Key (Opcional)", type="password")
    if api_k: st.session_state['api_key'] = api_k
    use_g = st.checkbox("Usar Tráfico Real", value=False, disabled=not bool(api_k))
    
    st.divider()
    st.markdown("### 🌎 Generador de Escenarios")
    paises = ["Mexico", "Colombia", "Brasil", "Argentina", "Chile", "Peru", "Ecuador", "Panama", "Guatemala"]
    pais_sel = st.selectbox("Seleccionar País", paises)
    
    if st.button(f"Generar Simulación {pais_sel}"):
        d = generar_escenario_pais(pais_sel)
        st.download_button(f"⬇️ Descargar Datos {pais_sel}", d, f"Simulacion_{pais_sel}.xlsx")

st.title("🚛 SaaS Logístico LATAM")

f = st.file_uploader("Cargar Archivo de Simulación", type=['xlsx'])
if f:
    dp, dc = smart_load(f)
    if not dp.empty:
        if st.button("🚀 Optimizar Operación"):
            res = solve_engine(dp, dc, use_g, st.session_state['api_key'])
            if not res.empty:
                res['Hora Entrada'] = res['Inicio Servicio'].apply(format_time)
                res['Hora Salida'] = res['Fin Servicio'].apply(format_time)
                st.session_state['results_df'] = res

if st.session_state['results_df'] is not None:
    df = st.session_state['results_df']
    st.divider()
    
    errs = audit_schedule(df)
    if errs.empty: st.success("✅ Auditoría: APROBADA (0 Solapamientos)")
    else: st.error(f"❌ ALERTA: {len(errs)} Choques"); st.dataframe(errs)

    t1, t2, t3, t4 = st.tabs(["🏭 Gantt General", "📦 Rastreo", "🔬 Inspector", "📥 Reportes"])
    
    with t1:
        c = alt.Chart(df).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', y='Nodo', color='Skill', row='Skill',
            tooltip=['Orden', 'Etiqueta Muelle']
        ).properties(width=700).interactive()
        st.altair_chart(c)
        
    with t2:
        sel = st.multiselect("Filtrar Pedido:", df['Orden'].unique())
        dv = df[df['Orden'].isin(sel)] if sel else df
        c = alt.Chart(dv).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', y='Orden', color='Tipo',
            tooltip=['Nodo', 'Etiqueta Muelle']
        ).properties(width=700).interactive()
        st.altair_chart(c)

    with t3:
        n = st.selectbox("Nodo:", df['Nodo'].unique())
        dn = df[df['Nodo'] == n]
        c = alt.Chart(dn).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', 
            y=alt.Y('Etiqueta Muelle', title='Muelle Real'),
            color='Skill', tooltip=['Orden', 'Hora Entrada']
        ).properties(width=700, height=300).interactive()
        st.altair_chart(c)
        st.dataframe(dn[['Orden', 'Skill', 'Etiqueta Muelle', 'Hora Entrada', 'Hora Salida']].sort_values('Hora Entrada'))

    with t4:
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as w: df.to_excel(w, index=False)
        st.download_button("Descargar Resultados", out.getvalue(), "Plan_Final.xlsx")
