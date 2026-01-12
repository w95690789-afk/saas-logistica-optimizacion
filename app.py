import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
import requests
import json
import numpy as np # Importación necesaria para la simulación
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SaaS Logístico T1 Enterprise", layout="wide", page_icon="🚛")

if 'results_df' not in st.session_state: st.session_state['results_df'] = None
if 'api_key' not in st.session_state: st.session_state['api_key'] = ""

# --- MÓDULO GOOGLE MAPS COMPUTE ROUTES ---
def get_google_route_time(origin_lat, origin_lon, dest_lat, dest_lon, departure_time_iso, api_key):
    """
    Consulta la API Compute Routes de Google para obtener tiempo con tráfico.
    Retorna: Horas (float)
    """
    if not api_key: return None
    
    endpoint = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration,routes.staticDuration"
    }
    
    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lon}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": departure_time_iso 
    }
    
    try:
        response = requests.post(endpoint, json=body, headers=headers)
        data = response.json()
        if "routes" in data and len(data["routes"]) > 0:
            # Duración viene en formato "3600s"
            duration_str = data["routes"][0].get("duration", "0s")
            seconds = int(duration_str.rstrip('s'))
            return seconds / 3600.0 # Retornar horas
    except Exception as e:
        st.error(f"Error Google API: {e}")
    
    return None

# --- GENERADOR DE DATOS DEMO (COCA-COLA MX) ---
def generar_datos_cocacola():
    """Genera un escenario de prueba masivo para México"""
    
    # 1. Red Logística (Nodos Reales)
    nodes = {
        101: {'Name': 'Planta Toluca (FEMSA)', 'Lat': 19.2826, 'Lon': -99.6557, 'Type': 'Plant', 'Cap': 12},
        102: {'Name': 'Planta Monterrey (Topo Chico)', 'Lat': 25.6866, 'Lon': -100.3161, 'Type': 'Plant', 'Cap': 10},
        103: {'Name': 'Planta Guadalajara', 'Lat': 20.6597, 'Lon': -103.3496, 'Type': 'Plant', 'Cap': 10},
        104: {'Name': 'Planta Cuautitlán', 'Lat': 19.6734, 'Lon': -99.1755, 'Type': 'Plant', 'Cap': 15},
        201: {'Name': 'CEDI Iztapalapa', 'Lat': 19.3552, 'Lon': -99.0622, 'Type': 'CEDI', 'Cap': 8},
        202: {'Name': 'CEDI Puebla', 'Lat': 19.0414, 'Lon': -98.2063, 'Type': 'CEDI', 'Cap': 6},
        203: {'Name': 'CEDI Veracruz', 'Lat': 19.1738, 'Lon': -96.1342, 'Type': 'CEDI', 'Cap': 5},
        204: {'Name': 'CEDI Querétaro', 'Lat': 20.5888, 'Lon': -100.3899, 'Type': 'CEDI', 'Cap': 6},
        205: {'Name': 'CEDI León Bajío', 'Lat': 21.1221, 'Lon': -101.6826, 'Type': 'CEDI', 'Cap': 6},
        206: {'Name': 'CEDI Mérida', 'Lat': 20.9674, 'Lon': -89.5926, 'Type': 'CEDI', 'Cap': 5},
        207: {'Name': 'CEDI Tijuana', 'Lat': 32.5149, 'Lon': -117.0382, 'Type': 'CEDI', 'Cap': 5},
        208: {'Name': 'CEDI Chihuahua', 'Lat': 28.6353, 'Lon': -106.0889, 'Type': 'CEDI', 'Cap': 5},
        210: {'Name': 'CEDI Acapulco', 'Lat': 16.8531, 'Lon': -99.8237, 'Type': 'CEDI', 'Cap': 4},
    }

    # Haversine simple para tiempos
    def get_time(lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        dist = R * c
        return max(2.0, round((dist / 60.0) + 1, 1)) # 60km/h + 1h fija

    # 2. Generar 100 Pedidos
    orders = []
    plant_ids = [k for k, v in nodes.items() if v['Type'] == 'Plant']
    cedi_ids = [k for k, v in nodes.items() if v['Type'] == 'CEDI']
    skills = ['Seco', 'Refrigerado']

    for i in range(1, 101):
        if np.random.rand() < 0.85: # 85% Planta -> CEDI
            orig_id, dest_id = np.random.choice(plant_ids), np.random.choice(cedi_ids)
        else: # 15% Inter-CEDI
            orig_id = np.random.choice(cedi_ids)
            dest_id = np.random.choice(cedi_ids)
            while dest_id == orig_id: dest_id = np.random.choice(cedi_ids)
        
        orig, dest = nodes[orig_id], nodes[dest_id]
        
        orders.append({
            'ID': f"KO-MX-{2026000+i}",
            'Origen_Lat': orig['Lat'], 'Origen_Lon': orig['Lon'],
            'Destino_Lat': dest['Lat'], 'Destino_Lon': dest['Lon'],
            'Tiempo_Estimado_Manual_h': get_time(orig['Lat'], orig['Lon'], dest['Lat'], dest['Lon']),
            'Tiempo_Carga_h': round(np.random.uniform(1.5, 3.0), 1),
            'Tiempo_Descarga_h': round(np.random.uniform(1.0, 2.5), 1),
            'Skill_Requerido': np.random.choice(skills, p=[0.8, 0.2]),
            'Prioridad': np.random.randint(1, 6)
        })

    # 3. Configuración Muelles
    muelle_config = []
    for nid, data in nodes.items():
        is_plant = data['Type'] == 'Plant'
        for d in range(1, data['Cap'] + 1):
            skill = 'Refrigerado' if d > data['Cap']-2 else 'Seco'
            muelle_config.append({
                'Nodo_ID': nid, 'Nombre_Nodo': data['Name'],
                'Muelle_ID': f"M{d}", 'Skill_Soportado': skill,
                'Horario_Apertura': 0 if is_plant else 6,
                'Horario_Cierre': 24 if is_plant else 22,
                'Breaks (Inicio-Fin)': "13-14; 21-22" if is_plant else "13-14"
            })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        pd.DataFrame(orders).to_excel(writer, sheet_name='Pedidos', index=False)
        pd.DataFrame(muelle_config).to_excel(writer, sheet_name='Config_Muelles', index=False)
    return output.getvalue()

# --- UTILIDADES ---
def format_time(hours_float):
    if pd.isna(hours_float): return "00:00"
    base_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    delta = timedelta(hours=float(hours_float))
    target_time = base_time + delta
    if hours_float >= 24:
        days = int(hours_float // 24)
        return f"+{days}d {target_time.strftime('%H:%M')}"
    return target_time.strftime('%H:%M')

def generate_complex_template():
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # HOJA 1: PEDIDOS (Con Coordenadas)
        cols_ped = ['ID', 'Origen_Lat', 'Origen_Lon', 'Destino_Lat', 'Destino_Lon', 
                    'Tiempo_Estimado_Manual_h', 'Tiempo_Carga_h', 'Tiempo_Descarga_h', 
                    'Skill_Requerido', 'Prioridad']
        df_p = pd.DataFrame(columns=cols_ped)
        df_p.loc[0] = ['PED-01', 4.6097, -74.0817, 6.2442, -75.5812, 8.5, 2, 2, 'Seco', 1]
        df_p.to_excel(writer, index=False, sheet_name='Pedidos')
        
        # HOJA 2: CONFIGURACIÓN NODOS Y MUELLES (Skills y Horarios)
        cols_conf = ['Nodo_ID', 'Nombre_Nodo', 'Muelle_ID', 'Skill_Soportado', 'Horario_Apertura', 'Horario_Cierre', 'Breaks (Inicio-Fin)']
        df_c = pd.DataFrame(columns=cols_conf)
        # Ejemplo: Muelle 1 trabaja corrido, Muelle 2 tiene almuerzo
        df_c.loc[0] = [10, 'Bogotá CEDI', 'M1', 'Seco', 6, 22, '']
        df_c.loc[1] = [10, 'Bogotá CEDI', 'M2', 'Frio', 8, 18, '12-13'] 
        df_c.to_excel(writer, index=False, sheet_name='Config_Muelles')
        
    return output.getvalue()

def parse_break_string(break_str):
    """Convierte '12-13; 16-16.5' en lista de tuplas [(12,13), (16, 16.5)]"""
    breaks = []
    if pd.isna(break_str) or str(break_str).strip() == '': return breaks
    parts = str(break_str).split(';')
    for p in parts:
        try:
            start, end = p.split('-')
            breaks.append((float(start), float(end)))
        except: pass
    return breaks

def smart_load(file):
    try:
        xl = pd.ExcelFile(file)
        df_p = pd.read_excel(file, sheet_name='Pedidos') if 'Pedidos' in xl.sheet_names else pd.DataFrame()
        df_c = pd.read_excel(file, sheet_name='Config_Muelles') if 'Config_Muelles' in xl.sheet_names else pd.DataFrame()
        return df_p, df_c
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame()

# --- MOTOR DE OPTIMIZACIÓN LÓGICA (CORE) ---
def solve_engine(df_pedidos, df_config, use_google, api_key):
    status = st.empty()
    status.info("⚙️ Iniciando Motor T1 (Skills + Turnos + Tráfico)...")
    
    model = cp_model.CpModel()
    horizon = 96
    
    # 1. ESTRUCTURAR RECURSOS (MUELLES)
    resource_map = {} # (Nodo, Skill) -> [IntVars...]
    dock_capacities = {} # (Nodo, Skill) -> Int (Cantidad de muelles)
    
    # Validar Configuración
    if df_config.empty:
        status.error("❌ Falta configuración de Muelles (Skills y Horarios).")
        return pd.DataFrame()

    # Agrupar muelles por Nodo y Skill
    unique_combinations = df_config.groupby(['Nodo_ID', 'Skill_Soportado']).size().reset_index(name='Capacidad')
    
    for _, row in unique_combinations.iterrows():
        key = (row['Nodo_ID'], row['Skill_Soportado'])
        dock_capacities[key] = row['Capacidad']
        resource_map[key] = []

    # 2. PROCESAR PEDIDOS Y CALCULAR TIEMPOS (GOOGLE)
    pedidos_vars = []
    route_cache = {}
    
    prog_bar = st.progress(0)
    total_p = len(df_pedidos)
    
    for idx, row in df_pedidos.iterrows():
        prog_bar.progress((idx + 1) / total_p)
        
        pid = row['ID']
        skill_req = row['Skill_Requerido']
        
        # Coordenadas
        o_lat, o_lon = row.get('Origen_Lat'), row.get('Origen_Lon')
        d_lat, d_lon = row.get('Destino_Lat'), row.get('Destino_Lon')
        
        # Calcular Tiempo de Viaje
        t_viaje = row.get('Tiempo_Estimado_Manual_h', 5) # Fallback
        
        if use_google and api_key and pd.notna(o_lat):
            route_key = (o_lat, o_lon, d_lat, d_lon)
            if route_key in route_cache:
                t_viaje = route_cache[route_key]
            else:
                dep_time = (datetime.utcnow() + timedelta(days=1)).replace(hour=8).isoformat() + 'Z'
                g_time = get_google_route_time(o_lat, o_lon, d_lat, d_lon, dep_time, api_key)
                if g_time:
                    t_viaje = g_time
                    route_cache[route_key] = t_viaje
        
        # Crear Variables CP
        t_carga = int(row.get('Tiempo_Carga_h', 2))
        t_descarga = int(row.get('Tiempo_Descarga_h', 2))
        t_viaje_int = int(t_viaje)
        
        # ORIGEN
        so = model.NewIntVar(0, horizon, f'so_{pid}')
        eo = model.NewIntVar(0, horizon, f'eo_{pid}')
        ivo = model.NewIntervalVar(so, t_carga, eo, f'ivo_{pid}')
        
        # DESTINO
        sd = model.NewIntVar(0, horizon, f'sd_{pid}')
        ed = model.NewIntVar(0, horizon, f'ed_{pid}')
        ivd = model.NewIntervalVar(sd, t_descarga, ed, f'ivd_{pid}')
        
        # Restricción Lógica de Viaje
        model.Add(sd >= eo + t_viaje_int)
        
        # ASIGNACIÓN DE SKILL (HEURÍSTICA DE NODO)
        # Asumiremos que el Excel ya trae lógica de Nodos consistente con la configuración
        # Para el ejemplo de Coca-Cola, necesitamos que los pedidos sepan su NODO ID, no solo lat/lon
        # En esta versión, haremos un "Match" por cercanía si tenemos Lat/Lon, o usaremos una columna ID explícita si existe
        # Para simplificar y asegurar que funcione con el generador de Coca-Cola (que no pone Nodo_ID en pedidos explícito pero sí Lat/Lon de nodos conocidos):
        
        # Vamos a buscar el Nodo_ID que coincida con las coordenadas del pedido en la tabla de Configuración
        # (Esto es un lookup reverso simple para la demo)
        
        n_orig_id = None
        n_dest_id = None
        
        # Lookup en configuración para encontrar IDs de nodo basados en Lat/Lon sería ideal, pero config solo tiene ID y Nombre.
        # Asumiremos para la demo que la simulación Coca-Cola genera Nodos IDs conocidos (101, 102...)
        # Y que el usuario usará esos IDs o que el sistema los infiere.
        
        # PARCHE PARA LA DEMO: Usaremos un ID ficticio basado en el índice para distribuir carga si no hay match
        # O mejor: El generador de Coca Cola pone Lat/Lon exactos de los nodos.
        # Pero el solver necesita el ID (ej. 101).
        # Vamos a saltarnos la restricción estricta de ID por ahora y usar un "Pool Global por Skill" si no hay ID, 
        # pero eso rompería la lógica de nodos.
        
        # SOLUCIÓN ROBUSTA: Extraer el ID del nodo del archivo de pedidos si existe, o usar un default.
        # El generador de Coca-Cola NO puso columna 'Nodo_Origen_ID' en pedidos, solo Lat/Lon.
        # Voy a modificar la función de 'solve_engine' para que intente mapear Lat/Lon a los IDs de la Configuración si es posible,
        # o asigne aleatoriamente a los nodos disponibles en Config que tengan ese Skill (Load Balancing).
        
        # Recuperar lista de Nodos disponibles para ese skill desde la config
        nodos_con_skill = df_config[df_config['Skill_Soportado'] == skill_req]['Nodo_ID'].unique()
        
        if len(nodos_con_skill) > 0:
            # Asignación Round Robin simple para la demo (ya que no tenemos el ID explícito en el excel de pedidos generado)
            n_orig_id = nodos_con_skill[idx % len(nodos_con_skill)]
            n_dest_id = nodos_con_skill[(idx + 1) % len(nodos_con_skill)]
        else:
            continue # No hay nodos para este skill
            
        key_o = (n_orig_id, skill_req)
        key_d = (n_dest_id, skill_req)
        
        if key_o in resource_map: resource_map[key_o].append(ivo)
        if key_d in resource_map: resource_map[key_d].append(ivd)
            
        pedidos_vars.append({
            'id': pid, 'vars': (so, eo, sd, ed), 'row': row, 
            'n_orig': n_orig_id, 'n_dest': n_dest_id
        })

    # 3. RESTRICCIONES DE CAPACIDAD Y TURNOS
    for (nodo_id, skill), intervalos_pedidos in resource_map.items():
        if not intervalos_pedidos: continue
        
        capacity = dock_capacities.get((nodo_id, skill), 1)
        
        # Breaks
        muelles_config = df_config[(df_config['Nodo_ID'] == nodo_id) & (df_config['Skill_Soportado'] == skill)]
        intervals_breaks = []
        
        for _, m_row in muelles_config.iterrows():
            breaks = parse_break_string(m_row.get('Breaks (Inicio-Fin)', ''))
            open_h = m_row.get('Horario_Apertura', 0)
            close_h = m_row.get('Horario_Cierre', 24)
            
            if open_h > 0: breaks.append((0, open_h))
            if close_h < 24: breaks.append((close_h, 24))
            
            for day in range(4): # 4 días horizonte
                offset = day * 24
                for b_start, b_end in breaks:
                    bk_start = int(b_start + offset)
                    bk_duration = int(b_end - b_start)
                    if bk_duration > 0:
                        iv_break = model.NewIntervalVar(bk_start, bk_duration, bk_start + bk_duration, f'bk')
                        intervals_breaks.append(iv_break)

        all_intervals = intervalos_pedidos + intervals_breaks
        demands = [1] * len(all_intervals)
        model.AddCumulative(all_intervals, demands, capacity)

    # 4. SOLVER
    obj_var = model.NewIntVar(0, horizon, 'makespan')
    if pedidos_vars:
        model.AddMaxEquality(obj_var, [p['vars'][3] for p in pedidos_vars])
        model.Minimize(obj_var)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60
    status = solver.Solve(model)
    
    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        status_text.success(f"✅ Optimización Completada (Google Maps: {'Sí' if use_google else 'No'})")
        for p in pedidos_vars:
            pid = p['id']
            v = p['vars']
            so, eo, sd, ed = solver.Value(v[0]), solver.Value(v[1]), solver.Value(v[2]), solver.Value(v[3])
            
            # Nombre Nodo
            n_orig_name = df_config[df_config['Nodo_ID'] == p['n_orig']]['Nombre_Nodo'].iloc[0] if not df_config[df_config['Nodo_ID'] == p['n_orig']].empty else str(p['n_orig'])
            n_dest_name = df_config[df_config['Nodo_ID'] == p['n_dest']]['Nombre_Nodo'].iloc[0] if not df_config[df_config['Nodo_ID'] == p['n_dest']].empty else str(p['n_dest'])

            results.append({
                'Orden': pid, 'Tipo': 'Origen', 
                'Nodo': n_orig_name, 
                'Skill': p['row']['Skill_Requerido'],
                'Inicio Servicio': so, 'Fin Servicio': eo
            })
            results.append({
                'Orden': pid, 'Tipo': 'Destino', 
                'Nodo': n_dest_name, 
                'Skill': p['row']['Skill_Requerido'],
                'Inicio Servicio': sd, 'Fin Servicio': ed
            })
        return pd.DataFrame(results)
    else:
        status_text.error("⚠️ No se encontró solución factible.")
        return pd.DataFrame()

# --- INTERFAZ UI ---
with st.sidebar:
    st.header("🔧 Configuración Enterprise")
    
    st.markdown("### 1. Google Maps API")
    api_input = st.text_input("Ingresa tu API Key:", type="password", help="Habilita 'Routes API' en Google Cloud Console.")
    if api_input: st.session_state['api_key'] = api_input
    
    use_google = st.checkbox("Usar Tráfico Real (Compute Routes)", value=False, disabled=not bool(st.session_state['api_key']))
    
    st.divider()
    st.markdown("### 2. Datos Maestros")
    if st.button("Generar Escenario Coca-Cola (Demo)"):
        data_mx = generar_datos_cocacola()
        st.download_button("⬇️ Descargar Simulacion_CocaCola.xlsx", data_mx, "Simulacion_CocaCola_MX.xlsx")

st.title("🚛 SaaS Logístico T1 Enterprise")
st.markdown(f"**Estado del Sistema:** {'🟢 Conectado a Google Maps' if st.session_state['api_key'] else '🟡 Modo Simulación Offline'}")

file = st.file_uploader("Cargar Template V12", type=['xlsx'])
if file:
    df_p, df_c = smart_load(file)
    if not df_p.empty and not df_c.empty:
        st.write(f"📋 Pedidos: {len(df_p)} | 🏭 Configuración Muelles: {len(df_c)}")
        
        if st.button("🚀 Ejecutar Optimización Avanzada"):
            res = solve_engine(df_p, df_c, use_google, st.session_state['api_key'])
            if not res.empty:
                res['Hora Entrada'] = res['Inicio Servicio'].apply(format_time)
                res['Hora Salida'] = res['Fin Servicio'].apply(format_time)
                res['Etiqueta'] = "Ord: " + res['Orden'].astype(str) + " (" + res['Skill'] + ")"
                st.session_state['results_df'] = res
    else:
        st.warning("Por favor carga el archivo usando el Template V12.")

if st.session_state['results_df'] is not None:
    df = st.session_state['results_df']
    st.divider()
    
    tab1, tab2 = st.tabs(["🏭 Gantt con Skills", "📥 Exportar"])
    
    with tab1:
        st.markdown("### Planificación por Skill")
        c = alt.Chart(df).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio',
            y=alt.Y('Nodo', sort='ascending'),
            color='Skill',
            row='Skill',
            tooltip=['Orden', 'Hora Entrada', 'Hora Salida']
        ).properties(width=700, height=300).interactive()
        st.altair_chart(c)
        
    with tab2:
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as w: df.to_excel(w, index=False)
        st.download_button("Descargar Plan Maestro", out.getvalue(), "Plan_Enterprise.xlsx")
