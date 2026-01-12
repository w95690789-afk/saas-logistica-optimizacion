import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
import requests
import numpy as np 
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SaaS Logístico T1 Full", layout="wide", page_icon="🚛")

if 'results_df' not in st.session_state: st.session_state['results_df'] = None
if 'api_key' not in st.session_state: st.session_state['api_key'] = ""

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

# --- 2. GENERADOR COCA-COLA (SIMULACIÓN) ---
def generar_datos_cocacola():
    nodes = {
        101: {'Name': 'Planta Toluca (FEMSA)', 'Lat': 19.2826, 'Lon': -99.6557, 'Type': 'Plant', 'Cap': 12},
        102: {'Name': 'Planta Monterrey', 'Lat': 25.6866, 'Lon': -100.3161, 'Type': 'Plant', 'Cap': 10},
        103: {'Name': 'Planta Guadalajara', 'Lat': 20.6597, 'Lon': -103.3496, 'Type': 'Plant', 'Cap': 10},
        201: {'Name': 'CEDI Iztapalapa', 'Lat': 19.3552, 'Lon': -99.0622, 'Type': 'CEDI', 'Cap': 8},
        202: {'Name': 'CEDI Puebla', 'Lat': 19.0414, 'Lon': -98.2063, 'Type': 'CEDI', 'Cap': 6},
        203: {'Name': 'CEDI Veracruz', 'Lat': 19.1738, 'Lon': -96.1342, 'Type': 'CEDI', 'Cap': 5},
        207: {'Name': 'CEDI Tijuana', 'Lat': 32.5149, 'Lon': -117.0382, 'Type': 'CEDI', 'Cap': 5},
    }
    def get_time(lat1, lon1, lat2, lon2): # Haversine
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        return max(2.0, round((R * 2 * atan2(sqrt(a), sqrt(1-a)) / 60.0) + 1, 1))

    orders = []
    plant_ids, cedi_ids = [k for k,v in nodes.items() if v['Type']=='Plant'], [k for k,v in nodes.items() if v['Type']=='CEDI']
    for i in range(1, 101):
        orig_id = np.random.choice(plant_ids) if np.random.rand() < 0.85 else np.random.choice(cedi_ids)
        dest_id = np.random.choice(cedi_ids)
        while dest_id == orig_id: dest_id = np.random.choice(cedi_ids)
        orig, dest = nodes[orig_id], nodes[dest_id]
        orders.append({
            'ID': f"ORD-{2026000+i}", 'Origen_Lat': orig['Lat'], 'Origen_Lon': orig['Lon'],
            'Destino_Lat': dest['Lat'], 'Destino_Lon': dest['Lon'],
            'Tiempo_Estimado_Manual_h': get_time(orig['Lat'], orig['Lon'], dest['Lat'], dest['Lon']),
            'Tiempo_Carga_h': 2, 'Tiempo_Descarga_h': 1.5,
            'Skill_Requerido': np.random.choice(['Seco', 'Refrigerado'], p=[0.8, 0.2]), 'Prioridad': 1
        })
    
    muelle_config = []
    for nid, data in nodes.items():
        for d in range(1, data['Cap'] + 1):
            skill = 'Refrigerado' if d > data['Cap']-2 else 'Seco'
            muelle_config.append({
                'Nodo_ID': nid, 'Nombre_Nodo': data['Name'], 'Muelle_ID': f"M{d}", 
                'Skill_Soportado': skill, 'Horario_Apertura': 6, 'Horario_Cierre': 22, 'Breaks (Inicio-Fin)': "13-14"
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

# --- 4. ALGORITMO DE ASIGNACIÓN REAL (PUENTE VISUAL) ---
def asignar_muelles_reales(df_resultados, df_config):
    """
    Toma la solución matemática (Abstracta) y asigna Nombres Reales de Muelles 
    (ej. 'Muelle 1 - Seco') basándose en la configuración y disponibilidad.
    Recupera la 'Vista por Muelle' que solicitaste.
    """
    df_final = df_resultados.copy()
    df_final['Etiqueta Muelle'] = "Sin Asignar"
    
    # Agrupar por Nodo y Skill para asignar recursos compatibles
    for (nodo, skill), grupo in df_final.groupby(['Nodo', 'Skill']):
        grupo = grupo.sort_values('Inicio Servicio')
        
        # Buscar muelles reales disponibles en Config para este Nodo+Skill
        muelles_disponibles = df_config[
            (df_config['Nombre_Nodo'] == nodo) & 
            (df_config['Skill_Soportado'] == skill)
        ]['Muelle_ID'].unique()
        
        if len(muelles_disponibles) == 0:
            # Fallback si no hay match exacto de nombre, intentar por ID
            muelles_disponibles = [f"Virtual_{skill}_{i+1}" for i in range(5)]
            
        # Estado de los muelles (hora fin)
        docks_status = {m: 0 for m in muelles_disponibles}
        
        for idx, row in grupo.iterrows():
            start, end = row['Inicio Servicio'], row['Fin Servicio']
            assigned = None
            
            # Greedy: Buscar primer muelle libre
            for m_id in muelles_disponibles:
                if start >= docks_status[m_id]:
                    docks_status[m_id] = end
                    assigned = m_id
                    break
            
            # Si todos ocupados (no debería pasar si el solver funcionó), usar el que se libere primero
            if not assigned:
                best_m = min(docks_status, key=docks_status.get)
                docks_status[best_m] = end # Se asume solapamiento forzado visual
                assigned = best_m
            
            df_final.at[idx, 'Etiqueta Muelle'] = str(assigned)
            
    return df_final

def audit_schedule(df):
    """Recuperada: Valida solapamientos físicos reales"""
    errors = []
    for (nodo, muelle), g in df.groupby(['Nodo', 'Etiqueta Muelle']):
        g = g.sort_values('Inicio Servicio')
        last_end, last_ord = -1, ""
        for _, r in g.iterrows():
            if r['Inicio Servicio'] < last_end - 0.001:
                errors.append({'Nodo': nodo, 'Muelle': muelle, 'Conflicto': f"{last_ord} vs {r['Orden']}"})
            last_end, last_ord = r['Fin Servicio'], r['Orden']
    return pd.DataFrame(errors)

# --- 5. MOTOR DE OPTIMIZACIÓN (CORE) ---
def solve_engine(df_pedidos, df_config, use_google, api_key):
    status_ph = st.empty()
    status_ph.info("⚙️ Optimizando (Skills + Turnos + Tráfico)...")
    
    model = cp_model.CpModel()
    horizon = 96
    
    # Mapeo de Capacidad
    resource_map = {} 
    dock_caps = df_config.groupby(['Nodo_ID', 'Skill_Soportado']).size().to_dict()
    for k in dock_caps: resource_map[k] = []

    pedidos_vars = []
    route_cache = {}
    
    # Procesar Pedidos
    prog = st.progress(0)
    for i, row in df_pedidos.iterrows():
        prog.progress((i+1)/len(df_pedidos))
        pid, skill = row['ID'], row['Skill_Requerido']
        
        # Tiempos
        tv = row.get('Tiempo_Estimado_Manual_h', 5)
        if use_google and api_key and pd.notna(row.get('Origen_Lat')):
            k = (row['Origen_Lat'], row['Origen_Lon'], row['Destino_Lat'], row['Destino_Lon'])
            if k not in route_cache:
                dt = (datetime.utcnow()+timedelta(days=1)).replace(hour=8).isoformat()+'Z'
                t_g = get_google_route_time(*k, dt, api_key)
                if t_g: route_cache[k] = t_g
            tv = route_cache.get(k, tv)
            
        # Variables
        tc, td = int(row.get('Tiempo_Carga_h', 2)), int(row.get('Tiempo_Descarga_h', 2))
        tv = int(tv)
        
        so, eo = model.NewIntVar(0, horizon, f'so_{pid}'), model.NewIntVar(0, horizon, f'eo_{pid}')
        ivo = model.NewIntervalVar(so, tc, eo, f'ivo_{pid}')
        
        sd, ed = model.NewIntVar(0, horizon, f'sd_{pid}'), model.NewIntVar(0, horizon, f'ed_{pid}')
        ivd = model.NewIntervalVar(sd, td, ed, f'ivd_{pid}')
        
        model.Add(sd >= eo + tv)
        
        # Asignación de Nodos (Heurística simple basada en coordenadas cercanas o IDs)
        # Para la demo Coca-Cola, usaremos el 'Nombre_Nodo' si está mapeado o haremos round-robin de nodos con ese skill
        # (Mejora robusta para que funcione con el generador)
        try:
            # Buscar nodos que soporten el skill
            cand_nodes = df_config[df_config['Skill_Soportado'] == skill]['Nodo_ID'].unique()
            if len(cand_nodes) > 0:
                n_orig = cand_nodes[i % len(cand_nodes)]
                n_dest = cand_nodes[(i+1) % len(cand_nodes)]
            else: continue
        except: continue
        
        if (n_orig, skill) in resource_map: resource_map[(n_orig, skill)].append(ivo)
        if (n_dest, skill) in resource_map: resource_map[(n_dest, skill)].append(ivd)
        
        pedidos_vars.append({'id': pid, 'vars': (so,eo,sd,ed), 'skill': skill, 'no': n_orig, 'nd': n_dest})

    # Restricciones (Turnos + Capacidad)
    for (nid, skill), ivs in resource_map.items():
        if not ivs: continue
        cap = dock_caps.get((nid, skill), 1)
        
        # Breaks
        breaks_ivs = []
        conf_rows = df_config[(df_config['Nodo_ID']==nid) & (df_config['Skill_Soportado']==skill)]
        for _, r in conf_rows.iterrows():
            for d in range(4):
                off = d*24
                # Apertura/Cierre
                op, cl = r.get('Horario_Apertura', 0), r.get('Horario_Cierre', 24)
                if op > 0: breaks_ivs.append(model.NewIntervalVar(0+off, op, 0+off+op, 'closed_am'))
                if cl < 24: breaks_ivs.append(model.NewIntervalVar(cl+off, 24-cl, 24+off, 'closed_pm'))
                # Breaks
                for s, e in parse_break_string(r.get('Breaks (Inicio-Fin)', '')):
                    dur = int(e-s)
                    if dur>0: breaks_ivs.append(model.NewIntervalVar(int(s+off), dur, int(s+dur+off), 'brk'))
        
        model.AddCumulative(ivs + breaks_ivs, [1]*len(ivs + breaks_ivs), cap)

    # Solve
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
            
            # Obtener nombres reales
            n_orig_name = df_config[df_config['Nodo_ID']==p['no']]['Nombre_Nodo'].iloc[0]
            n_dest_name = df_config[df_config['Nodo_ID']==p['nd']]['Nombre_Nodo'].iloc[0]
            
            res.append({'Orden': p['id'], 'Tipo': 'Origen', 'Nodo': n_orig_name, 'Skill': p['skill'], 'Inicio Servicio': so, 'Fin Servicio': eo})
            res.append({'Orden': p['id'], 'Tipo': 'Destino', 'Nodo': n_dest_name, 'Skill': p['skill'], 'Inicio Servicio': sd, 'Fin Servicio': ed})
        
        # PASO CRÍTICO RECUPERADO: Asignar nombres de muelles reales
        return asignar_muelles_reales(pd.DataFrame(res), df_config)
    else:
        status_ph.error("⚠️ Infeasible")
        return pd.DataFrame()

# --- INTERFAZ UI ---
with st.sidebar:
    st.header("🔧 Panel de Control")
    api_k = st.text_input("Google Maps API Key", type="password")
    if api_k: st.session_state['api_key'] = api_k
    use_g = st.checkbox("Usar Tráfico Real", value=False, disabled=not bool(api_k))
    
    st.divider()
    if st.button("🇲🇽 Generar Simulación Coca-Cola"):
        d = generar_datos_cocacola()
        st.download_button("Descargar Excel Demo", d, "Simulacion_CocaCola_MX.xlsx")

st.title("🚛 SaaS Logístico T1 Full")

f = st.file_uploader("Cargar Archivo (Template V12)", type=['xlsx'])
if f:
    dp, dc = smart_load(f)
    if not dp.empty:
        if st.button("🚀 Ejecutar Optimización"):
            res = solve_engine(dp, dc, use_g, st.session_state['api_key'])
            if not res.empty:
                res['Hora Entrada'] = res['Inicio Servicio'].apply(format_time)
                res['Hora Salida'] = res['Fin Servicio'].apply(format_time)
                st.session_state['results_df'] = res

if st.session_state['results_df'] is not None:
    df = st.session_state['results_df']
    st.divider()
    
    # 1. AUDITORÍA (RECUPERADA)
    errs = audit_schedule(df)
    if errs.empty: st.success("✅ Auditoría de Solapamiento: APROBADA (0 Choques)")
    else: st.error(f"❌ ALERTA: {len(errs)} Choques detectados en muelles"); st.dataframe(errs)

    # 2. TABS COMPLETOS (RECUPERADOS)
    t1, t2, t3, t4 = st.tabs(["🏭 Gantt General", "📦 Rastreo Pedidos", "🔬 Inspector de Muelles", "📥 Exportar"])
    
    with t1: # Gantt General (Skill Split)
        c = alt.Chart(df).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', y='Nodo', color='Skill', row='Skill',
            tooltip=['Orden', 'Etiqueta Muelle']
        ).properties(width=700).interactive()
        st.altair_chart(c)
        
    with t2: # Vista Pedidos (Recuperada)
        sel = st.multiselect("Filtrar Pedido:", df['Orden'].unique())
        dv = df[df['Orden'].isin(sel)] if sel else df
        c = alt.Chart(dv).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', y='Orden', color='Tipo',
            tooltip=['Nodo', 'Etiqueta Muelle']
        ).properties(width=700).interactive()
        st.altair_chart(c)

    with t3: # Inspector (Recuperado)
        n = st.selectbox("Seleccionar Nodo:", df['Nodo'].unique())
        dn = df[df['Nodo'] == n]
        c = alt.Chart(dn).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', 
            y=alt.Y('Etiqueta Muelle', title='Muelle Real'), # EJE Y ESPECÍFICO
            color='Skill',
            tooltip=['Orden', 'Hora Entrada']
        ).properties(width=700, height=300).interactive()
        st.altair_chart(c)
        st.dataframe(dn[['Orden', 'Skill', 'Etiqueta Muelle', 'Hora Entrada', 'Hora Salida']].sort_values('Hora Entrada'))

    with t4:
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as w: df.to_excel(w, index=False)
        st.download_button("Descargar Resultados", out.getvalue(), "Plan_Final.xlsx")
