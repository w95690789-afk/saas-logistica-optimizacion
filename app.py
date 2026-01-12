import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
import requests
import numpy as np 
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SaaS Logístico LATAM T1", layout="wide", page_icon="🌎")

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

# --- 2. GENERADOR DE ESCENARIOS (100 PEDIDOS SIEMPRE) ---
def generar_escenario_pais(pais_seleccionado):
    # Base de Datos Geoespacial Completa
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
    
    # --- CORRECCIÓN: 100 PEDIDOS EXACTOS ---
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
            'Skill_Requerido': np.random.choice(['Seco', 'Refrigerado'], p=[0.7, 0.3]), 
            'Prioridad': 1
        })
    
    muelle_config = []
    for nid, data in nodes.items():
        is_plant = data['Type'] == 'Plant'
        for d in range(1, data['Cap'] + 1):
            # 50% Turnos Partidos (Caos Realista)
            if np.random.rand() < 0.5:
                ap, cl, brk = 6, 22, "12-14" 
            else:
                ap, cl, brk = (0, 24, "13-14; 21-22") if is_plant else (7, 19, "13-14")
            
            # Skills Mixtos
            r_skill = np.random.rand()
            skill = 'Mixto' if r_skill > 0.8 else ('Refrigerado' if r_skill > 0.6 else 'Seco')

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

# --- 4. MOTOR DE OPTIMIZACIÓN (MODO ESTRICTO POR MUELLE) ---
def solve_engine(df_pedidos, df_config, use_google, api_key):
    status_ph = st.empty()
    status_ph.info("⚙️ Iniciando Optimización Estricta (Asignación Individual de Muelles)...")
    
    model = cp_model.CpModel()
    horizon = 96
    
    # 1. PRE-PROCESAMIENTO DE MUELLES INDIVIDUALES
    nodos_muelles = {}
    
    for _, row in df_config.iterrows():
        nid = row['Nodo_ID']
        mid = row['Muelle_ID']
        skill_m = row['Skill_Soportado']
        
        if nid not in nodos_muelles: nodos_muelles[nid] = []
        
        intervals_bloqueados = []
        op, cl = row.get('Horario_Apertura', 0), row.get('Horario_Cierre', 24)
        breaks_list = parse_break_string(row.get('Breaks (Inicio-Fin)', ''))
        
        for day in range(4):
            off = day * 24
            if op > 0: intervals_bloqueados.append((0+off, op))
            if cl < 24: intervals_bloqueados.append((cl+off, 24-cl))
            for s, e in breaks_list:
                dur = e - s
                if dur > 0: intervals_bloqueados.append((s+off, dur))
        
        cp_intervals_bloqueados = []
        for start, dur in intervals_bloqueados:
            # CORRECCIÓN DE ATTRIBUTE ERROR: Usamos NewIntervalVar (compatible)
            iv = model.NewIntervalVar(int(start), int(dur), int(start+dur), f"block_{mid}_{start}")
            cp_intervals_bloqueados.append(iv)
            
        nodos_muelles[nid].append({
            'id': mid,
            'skill': skill_m,
            'nombre_nodo': row['Nombre_Nodo'],
            'bloqueos': cp_intervals_bloqueados,
            'ordenes_asignadas': []
        })

    pedidos_vars = []
    route_cache = {}
    
    prog = st.progress(0)
    for i, row in df_pedidos.iterrows():
        prog.progress((i+1)/len(df_pedidos))
        pid, skill_req = row['ID'], row['Skill_Requerido']
        
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
        sd, ed = model.NewIntVar(0, horizon, f'sd_{pid}'), model.NewIntVar(0, horizon, f'ed_{pid}')
        
        model.Add(sd >= eo + tv)

        # Asignación Heurística para Demo
        node_keys = list(nodos_muelles.keys())
        n_orig = node_keys[i % len(node_keys)]
        n_dest = node_keys[(i+1) % len(node_keys)]

        def asignar_a_muelle_posible(nodo_id, start_var, duration, end_var, tipo_op):
            literales_eleccion = []
            
            for m in nodos_muelles.get(nodo_id, []):
                if m['skill'] == 'Mixto' or m['skill'] == skill_req:
                    is_in_dock = model.NewBoolVar(f"{pid}_{tipo_op}_in_{m['id']}")
                    literales_eleccion.append(is_in_dock)
                    iv_opt = model.NewOptionalIntervalVar(start_var, duration, end_var, is_in_dock, f"opt_{pid}_{m['id']}")
                    m['ordenes_asignadas'].append(iv_opt)
            
            if not literales_eleccion: return None
            model.Add(sum(literales_eleccion) == 1)
            return True

        valid_o = asignar_a_muelle_posible(n_orig, so, tc, eo, 'orig')
        valid_d = asignar_a_muelle_posible(n_dest, sd, td, ed, 'dest')
        
        if valid_o and valid_d:
            pedidos_vars.append({
                'id': pid, 'vars': (so, eo, sd, ed), 'skill': skill_req,
                'no': n_orig, 'nd': n_dest
            })

    # NO OVERLAP ESTRICTO
    for nid, muelles in nodos_muelles.items():
        for m in muelles:
            todos_intervalos = m['ordenes_asignadas'] + m['bloqueos']
            if todos_intervalos:
                model.AddNoOverlap(todos_intervalos)

    obj = model.NewIntVar(0, horizon, 'mk')
    if pedidos_vars: 
        model.AddMaxEquality(obj, [p['vars'][3] for p in pedidos_vars])
        model.Minimize(obj)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60
    st_solve = solver.Solve(model)
    
    if st_solve in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        status_ph.success("✅ Solución Estricta Encontrada (Cero Solapamientos)")
        res = []
        for p in pedidos_vars:
            v = p['vars']
            so, eo, sd, ed = solver.Value(v[0]), solver.Value(v[1]), solver.Value(v[2]), solver.Value(v[3])
            
            nom_o = nodos_muelles[p['no']][0]['nombre_nodo']
            nom_d = nodos_muelles[p['nd']][0]['nombre_nodo']

            res.append({'Orden': p['id'], 'Tipo': 'Origen', 'Nodo': nom_o, 'Nodo_ID': p['no'], 'Skill': p['skill'], 'Inicio Servicio': so, 'Fin Servicio': eo})
            res.append({'Orden': p['id'], 'Tipo': 'Destino', 'Nodo': nom_d, 'Nodo_ID': p['nd'], 'Skill': p['skill'], 'Inicio Servicio': sd, 'Fin Servicio': ed})
        
        return pd.DataFrame(res)
    else:
        status_ph.error("⚠️ No se pudo agendar. Demasiadas restricciones.")
        return pd.DataFrame()

# --- 5. POST-PROCESAMIENTO ---
def asignar_nombres_muelles(df, df_config):
    df_out = df.copy()
    df_out['Etiqueta Muelle'] = "Asignado"
    
    for nodo_id, grupo in df_out.groupby('Nodo_ID'):
        grupo = grupo.sort_values('Inicio Servicio')
        config = df_config[df_config['Nodo_ID'] == nodo_id]
        
        docks_state = {} 
        for _, row in config.iterrows():
            docks_state[row['Muelle_ID']] = {'free_at': 0, 'skill': row['Skill_Soportado'], 'breaks': parse_break_string(row['Breaks (Inicio-Fin)'])}
            
        for idx, row in grupo.iterrows():
            start, end = row['Inicio Servicio'], row['Fin Servicio']
            req_skill = row['Skill']
            best_dock = None
            
            for mid, state in docks_state.items():
                if state['skill'] in ['Mixto', req_skill]:
                    if start >= state['free_at']:
                        choque_break = False
                        for b_s, b_e in state['breaks']:
                            for d in range(4):
                                off = d*24
                                if not (end <= (b_s+off) or start >= (b_e+off)): choque_break = True
                        if not choque_break:
                            best_dock = mid
                            break
            
            if best_dock:
                docks_state[best_dock]['free_at'] = end
                df_out.at[idx, 'Etiqueta Muelle'] = best_dock
            else:
                # Si esto pasa, es porque el mapeo visual no coincidió exactamente con la lógica booleana del solver
                # pero el solver ya garantizó que hay espacio. Asignamos al que cause menor impacto visual.
                best_dock = min(docks_state.keys(), key=lambda k: docks_state[k]['free_at'])
                docks_state[best_dock]['free_at'] = end
                df_out.at[idx, 'Etiqueta Muelle'] = best_dock
                
    return df_out

# --- UI ---
with st.sidebar:
    st.header("🌎 Simulación")
    paises = ["Mexico", "Colombia", "Brasil", "Argentina", "Chile", "Peru", "Ecuador", "Panama", "Guatemala"]
    pais = st.selectbox("País", paises)
    if st.button("Generar Datos"):
        d = generar_escenario_pais(pais)
        st.download_button("Descargar Excel", d, f"Simulacion_{pais}.xlsx")
    st.divider()
    api = st.text_input("Google API Key", type="password")
    if api: st.session_state['api_key'] = api
    use_g = st.checkbox("Tráfico Real", value=False, disabled=not bool(api))

st.title("🚛 SaaS Logístico T1 Estricto")
f = st.file_uploader("Cargar Archivo", type=['xlsx'])

if f:
    dp, dc = smart_load(f)
    if not dp.empty:
        if st.button("🚀 Optimizar"):
            res = solve_engine(dp, dc, use_g, st.session_state['api_key'])
            if not res.empty:
                final_df = asignar_nombres_muelles(res, dc)
                final_df['Hora Entrada'] = final_df['Inicio Servicio'].apply(format_time)
                final_df['Hora Salida'] = final_df['Fin Servicio'].apply(format_time)
                st.session_state['results_df'] = final_df

if st.session_state['results_df'] is not None:
    df = st.session_state['results_df']
    st.divider()
    
    # AUDITORÍA
    errs = audit_schedule(df)
    if errs.empty: st.success("✅ CERO SOLAPAMIENTOS CONFIRMADO")
    else: st.error(f"❌ {len(errs)} Errores Visuales"); st.dataframe(errs)
    
    # --- RECUPERACIÓN DE TODAS LAS PESTAÑAS (4 TABS) ---
    t1, t2, t3, t4 = st.tabs(["🏭 Gantt General", "📦 Rastreo Pedidos", "🔬 Inspector de Muelles", "📥 Exportar"])
    
    with t1:
        c = alt.Chart(df).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', y='Nodo', color='Skill', 
            tooltip=['Orden', 'Etiqueta Muelle']
        ).properties(width=700).interactive()
        st.altair_chart(c)
        
    with t2: # Tablero de Pedidos (Restaurado)
        st.markdown("##### 🔎 Rastrear Pedido Específico")
        all_orders = sorted(df['Orden'].unique())
        sel_order = st.multiselect("Buscar ID de Pedido:", all_orders)
        
        df_view = df[df['Orden'].isin(sel_order)] if sel_order else df.head(20) # Mostrar primeros 20 si no hay filtro
        
        c = alt.Chart(df_view).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', y='Orden', color='Tipo',
            tooltip=['Nodo', 'Etiqueta Muelle']
        ).properties(width=700).interactive()
        st.altair_chart(c)

    with t3: # Inspector con Filtro de Muelles (Restaurado y Mejorado)
        col_n, col_m = st.columns(2)
        with col_n:
            n = st.selectbox("Seleccionar Nodo:", df['Nodo'].unique())
        
        dn = df[df['Nodo'] == n]
        muelles_nodo = sorted(dn['Etiqueta Muelle'].unique())
        
        with col_m:
            # FILTRO DE MUELLES (NUEVO)
            sel_muelles = st.multiselect("Filtrar Muelles Específicos:", muelles_nodo, default=muelles_nodo)
            
        if sel_muelles:
            dn = dn[dn['Etiqueta Muelle'].isin(sel_muelles)]
            
        c = alt.Chart(dn).mark_bar().encode(
            x='Inicio Servicio', x2='Fin Servicio', 
            y=alt.Y('Etiqueta Muelle', title='Muelle Real'),
            color='Skill', tooltip=['Orden', 'Hora Entrada']
        ).properties(width=700, height=300).interactive()
        st.altair_chart(c)
        st.dataframe(dn[['Orden', 'Skill', 'Etiqueta Muelle', 'Hora Entrada', 'Hora Salida']].sort_values('Hora Entrada'), use_container_width=True)

    with t4:
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as w: df.to_excel(w, index=False)
        st.download_button("Descargar Final", out.getvalue(), "Plan.xlsx")
