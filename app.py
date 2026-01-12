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

# --- BASE DE DATOS GEOGRÁFICA ---
DB_PAISES = {
    "Mexico": {
        101: {'Name': 'Planta Toluca Central', 'Lat': 19.2826, 'Lon': -99.6557, 'Type': 'Plant', 'Cap': 15},
        201: {'Name': 'CEDI Iztapalapa', 'Lat': 19.3552, 'Lon': -99.0622, 'Type': 'CEDI', 'Cap': 8},
        202: {'Name': 'CEDI Puebla', 'Lat': 19.0414, 'Lon': -98.2063, 'Type': 'CEDI', 'Cap': 6},
        204: {'Name': 'CEDI Querétaro', 'Lat': 20.5888, 'Lon': -100.3899, 'Type': 'CEDI', 'Cap': 6},
        207: {'Name': 'CEDI Tijuana', 'Lat': 32.5149, 'Lon': -117.0382, 'Type': 'CEDI', 'Cap': 5},
    },
    "Colombia": {
        101: {'Name': 'Planta Tocancipá', 'Lat': 4.9654, 'Lon': -73.9429, 'Type': 'Plant', 'Cap': 12},
        201: {'Name': 'CEDI Bogotá Sur', 'Lat': 4.5981, 'Lon': -74.1558, 'Type': 'CEDI', 'Cap': 8},
        202: {'Name': 'CEDI Medellín', 'Lat': 6.2442, 'Lon': -75.5812, 'Type': 'CEDI', 'Cap': 6},
        203: {'Name': 'CEDI Cali', 'Lat': 3.4516, 'Lon': -76.5320, 'Type': 'CEDI', 'Cap': 5},
    },
    # (Se pueden agregar más países con la misma lógica)
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

# --- 2. GENERADOR DE ESCENARIOS (MODO CAOS REALISTA) ---
def generar_escenario_pais(pais_seleccionado):
    nodes = DB_PAISES.get(pais_seleccionado, DB_PAISES["Mexico"]) 

    # Función auxiliar para generar horarios caóticos (Turnos Partidos)
    def generar_horario_complejo(es_planta):
        # 50% Probabilidad de Turno Partido (Caos)
        if np.random.rand() < 0.5:
            # Escenario A: Turno Mañana y Tarde separados (Gran hueco al medio)
            if np.random.rand() < 0.5:
                apertura, cierre = 6, 22
                breaks = "11-15" # 4 horas muerto al medio día
            # Escenario B: Turno Nocturno Partido
            else:
                apertura, cierre = 14, 24
                breaks = "18-19.5" # Cena larga
        else:
            # Horario Continuo "Normal"
            if es_planta:
                apertura, cierre = 0, 24
                breaks = "13-14; 21-22"
            else:
                apertura, cierre = 7, 19
                breaks = "12-13"
        return apertura, cierre, breaks

    def get_time_approx(lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, atan2
        R = 6371
        dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        return max(2.0, round((R * 2 * atan2(sqrt(a), sqrt(1-a)) / 50.0) + 1, 1)) # Velocidad conservadora

    orders = []
    keys = list(nodes.keys())
    
    # Generar 120 Pedidos (Más volumen para saturar los turnos partidos)
    for i in range(1, 121):
        orig_id = np.random.choice(keys)
        dest_id = np.random.choice(keys)
        while dest_id == orig_id: dest_id = np.random.choice(keys)
        
        orig, dest = nodes[orig_id], nodes[dest_id]
        
        # Skills aleatorios
        skill = np.random.choice(['Seco', 'Refrigerado'], p=[0.7, 0.3])
        
        orders.append({
            'ID': f"PED-{pais_seleccionado[:2].upper()}-{2026000+i}", 
            'Origen_Lat': orig['Lat'], 'Origen_Lon': orig['Lon'],
            'Destino_Lat': dest['Lat'], 'Destino_Lon': dest['Lon'],
            'Tiempo_Estimado_Manual_h': get_time_approx(orig['Lat'], orig['Lon'], dest['Lat'], dest['Lon']),
            'Tiempo_Carga_h': round(np.random.uniform(1.5, 3.5), 1), 
            'Tiempo_Descarga_h': round(np.random.uniform(1.0, 2.0), 1),
            'Skill_Requerido': skill, 
            'Prioridad': np.random.randint(1, 4)
        })
    
    # Generar Configuración de Muelles con "Turnos Partidos" y "Mixtos"
    muelle_config = []
    for nid, data in nodes.items():
        is_plant = data['Type'] == 'Plant'
        for d in range(1, data['Cap'] + 1):
            # Asignación de Skill más realista
            rand_val = np.random.rand()
            if rand_val < 0.5: skill = 'Seco'
            elif rand_val < 0.8: skill = 'Refrigerado'
            else: skill = 'Mixto' # 20% de muelles polivalentes
            
            # Generar Caos en el Horario
            ap, cl, brk = generar_horario_complejo(is_plant)
            
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

# --- 4. ALGORITMO DE ASIGNACIÓN REAL ---
def asignar_muelles_reales(df_resultados, df_config):
    df_final = df_resultados.copy()
    df_final['Etiqueta Muelle'] = "Virtual"
    
    # Agrupar por Nodo para asignación física
    for nodo_nombre, grupo in df_final.groupby('Nodo'):
        grupo = grupo.sort_values('Inicio Servicio')
        
        # Buscar todos los muelles de ese nodo en la config
        # Nota: Aquí permitimos cruce flexible. Si el pedido es Seco, puede ir a Muelle Seco o Mixto.
        config_nodo = df_config[df_config['Nombre_Nodo'] == nodo_nombre]
        
        docks_schedule = {} # Muelle_ID -> Hora Liberación
        
        # Inicializar muelles
        for _, row in config_nodo.iterrows():
            docks_schedule[row['Muelle_ID']] = 0
            
        # Si no hay muelles configurados, crear virtuales
        if not docks_schedule:
            docks_schedule = {f"V-{i}": 0 for i in range(5)}

        for idx, row in grupo.iterrows():
            skill_req = row['Skill']
            start, end = row['Inicio Servicio'], row['Fin Servicio']
            
            # Filtrar muelles compatibles
            candidatos = []
            for m_id in docks_schedule.keys():
                # Verificar skill en config
                m_skill = config_nodo[config_nodo['Muelle_ID'] == m_id]['Skill_Soportado'].values
                if len(m_skill) > 0:
                    s = m_skill[0]
                    # Lógica de Compatibilidad: Mixto acepta todo. Seco acepta Seco.
                    if s == 'Mixto' or s == skill_req:
                        candidatos.append(m_id)
            
            # Si no hay candidatos compatibles (fallback), usar todos
            if not candidatos: candidatos = list(docks_schedule.keys())

            # Asignar al primero libre
            assigned = None
            best_fit = None
            min_gap = float('inf')
            
            for m in candidatos:
                liberacion = docks_schedule[m]
                if start >= liberacion:
                    # Este muelle sirve. Buscamos el que deje menos hueco (Best Fit)
                    gap = start - liberacion
                    if gap < min_gap:
                        min_gap = gap
                        best_fit = m
            
            if best_fit:
                assigned = best_fit
            else:
                # Si todos ocupados, forzar al que se libere antes (causará solapamiento visual, alertando el problema)
                assigned = min(candidatos, key=lambda k: docks_schedule[k])
            
            docks_schedule[assigned] = end
            df_final.at[idx, 'Etiqueta Muelle'] = str(assigned)
            
    return df_final

def audit_schedule(df):
    errors = []
    # Validación más estricta
    for (nodo, muelle), g in df.groupby(['Nodo', 'Etiqueta Muelle']):
        g = g.sort_values('Inicio Servicio')
        last_end, last_ord = -1, ""
        for _, r in g.iterrows():
            # Tolerancia técnica
            if r['Inicio Servicio'] < last_end - 0.01: 
                errors.append({'Nodo': nodo, 'Muelle': muelle, 'Conflicto': f"{last_ord} vs {r['Orden']}"})
            last_end, last_ord = r['Fin Servicio'], r['Orden']
    return pd.DataFrame(errors)

# --- 5. MOTOR DE OPTIMIZACIÓN ---
def solve_engine(df_pedidos, df_config, use_google, api_key):
    status_ph = st.empty()
    status_ph.info("⚙️ Simulando Operación Compleja...")
    
    model = cp_model.CpModel()
    horizon = 96
    
    # Pre-procesar capacidad combinada (Seco + Mixto, etc) es complejo en CP puro.
    # Simplificación: Tratamos (Nodo, Skill) como recursos, y Mixto como recurso aparte.
    # El Solver asignará Skill a Skill. La flexibilidad de "Mixto" la manejamos en la asignación física final (post-proceso).
    # OJO: Para que la simulación no falle, generaremos pedidos 'Mixtos' o haremos que los pedidos 'Secos' puedan caer en bolsas 'Mixtas'.
    # ESTRATEGIA: Pool Único con Atributos.
    
    # Mapa de recursos
    resource_map = {} 
    dock_caps = {}

    # Calcular capacidad por Nodo/Skill
    # IMPORTANTE: Sumamos los 'Mixto' a la capacidad de los otros skills para el solver matemático
    for _, row in df_config.iterrows():
        nid, skill = row['Nodo_ID'], row['Skill_Soportado']
        key = (nid, skill)
        dock_caps[key] = dock_caps.get(key, 0) + 1
        resource_map[key] = []
        
        # Truco: Si es mixto, agrega capacidad virtual a Seco y Refrigerado también
        if skill == 'Mixto':
            for s_virt in ['Seco', 'Refrigerado']:
                k_v = (nid, s_virt)
                dock_caps[k_v] = dock_caps.get(k_v, 0) + 1
                if k_v not in resource_map: resource_map[k_v] = []

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
        
        # Match Geoespacial Simple
        # Buscamos en Config el nodo más cercano a las coordenadas del pedido
        # (Para esta demo, asumimos que el generador crea coordenadas exactas)
        # Búsqueda reversa:
        try:
            # Latitud con 4 decimales
            lat_orig_ped = round(row['Origen_Lat'], 4)
            # Buscar en config (Ojo: Config no tiene lat/lon, solo ID. Usamos el Generador como fuente de verdad)
            # Solución: En la demo, asignamos Nodos aleatorios compatibles con el skill.
            cand_nodes = df_config[df_config['Skill_Soportado'].isin([skill, 'Mixto'])]['Nodo_ID'].unique()
            if len(cand_nodes) > 0:
                n_orig = cand_nodes[i % len(cand_nodes)]
                n_dest = cand_nodes[(i+1) % len(cand_nodes)]
            else: continue
        except: continue
        
        # Asignar a Resource Map
        # Preferencia: Intentar asignar a Skill específico primero
        if (n_orig, skill) in resource_map: resource_map[(n_orig, skill)].append(ivo)
        elif (n_orig, 'Mixto') in resource_map: resource_map[(n_orig, 'Mixto')].append(ivo)
            
        if (n_dest, skill) in resource_map: resource_map[(n_dest, skill)].append(ivd)
        elif (n_dest, 'Mixto') in resource_map: resource_map[(n_dest, 'Mixto')].append(ivd)
        
        pedidos_vars.append({'id': pid, 'vars': (so,eo,sd,ed), 'skill': skill, 'no': n_orig, 'nd': n_dest})

    # Restricciones
    for (nid, skill), ivs in resource_map.items():
        if not ivs: continue
        # Capacidad real
        cap = dock_caps.get((nid, skill), 1)
        
        # Breaks (Complejos)
        muelles_cf = df_config[(df_config['Nodo_ID']==nid) & (df_config['Skill_Soportado']==skill)]
        breaks_ivs = []
        for _, r in muelles_cf.iterrows():
            # Turnos Partidos
            for d in range(4):
                off = d*24
                op, cl = r.get('Horario_Apertura', 0), r.get('Horario_Cierre', 24)
                if op > 0: breaks_ivs.append(model.NewIntervalVar(0+off, op, 0+off+op, 'closed_am'))
                if cl < 24: breaks_ivs.append(model.NewIntervalVar(cl+off, 24-cl, 24+off, 'closed_pm'))
                for s, e in parse_break_string(r.get('Breaks (Inicio-Fin)', '')):
                    dur = int(e-s)
                    if dur>0: breaks_ivs.append(model.NewIntervalVar(int(s+off), dur, int(s+dur+off), 'brk'))
        
        # Usamos Cumulative flexible (permitimos solapamiento controlado por capacidad)
        model.AddCumulative(ivs + breaks_ivs, [1]*len(ivs + breaks_ivs), cap)

    obj = model.NewIntVar(0, horizon, 'mk')
    if pedidos_vars: 
        model.AddMaxEquality(obj, [p['vars'][3] for p in pedidos_vars])
        model.Minimize(obj)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 45
    st_solve = solver.Solve(model)
    
    if st_solve in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        status_ph.success("✅ Simulación Exitosa")
        res = []
        for p in pedidos_vars:
            v = p['vars']
            so, eo, sd, ed = solver.Value(v[0]), solver.Value(v[1]), solver.Value(v[2]), solver.Value(v[3])
            
            # Nombre Nodo (Manejo de errores si no encuentra)
            try: n_orig_name = df_config[df_config['Nodo_ID']==p['no']]['Nombre_Nodo'].iloc[0]
            except: n_orig_name = str(p['no'])
            try: n_dest_name = df_config[df_config['Nodo_ID']==p['nd']]['Nombre_Nodo'].iloc[0]
            except: n_dest_name = str(p['nd'])
            
            res.append({'Orden': p['id'], 'Tipo': 'Origen', 'Nodo': n_orig_name, 'Skill': p['skill'], 'Inicio Servicio': so, 'Fin Servicio': eo})
            res.append({'Orden': p['id'], 'Tipo': 'Destino', 'Nodo': n_dest_name, 'Skill': p['skill'], 'Inicio Servicio': sd, 'Fin Servicio': ed})
        return asignar_muelles_reales(pd.DataFrame(res), df_config)
    else:
        status_ph.error("⚠️ Saturación de Turnos: Intenta reducir pedidos o ampliar horarios.")
        return pd.DataFrame()

# --- INTERFAZ UI ---
with st.sidebar:
    st.header("🌎 Generador de Escenarios")
    paises = ["Mexico", "Colombia", "Brasil", "Argentina", "Chile", "Peru"]
    pais_sel = st.selectbox("Seleccionar País", paises)
    
    if st.button(f"Generar Datos {pais_sel}"):
        d = generar_escenario_pais(pais_sel)
        st.download_button(f"⬇️ Descargar {pais_sel}.xlsx", d, f"Simulacion_{pais_sel}.xlsx")
    
    st.divider()
    api_k = st.text_input("Google API Key", type="password")
    if api_k: st.session_state['api_key'] = api_k
    use_g = st.checkbox("Tráfico Real", value=False, disabled=not bool(api_k))

st.title("🚛 SaaS Logístico LATAM")

f = st.file_uploader("Cargar Archivo Simulado", type=['xlsx'])
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
    if errs.empty: st.success("✅ Auditoría: APROBADA")
    else: st.error(f"❌ Conflictos Detectados: {len(errs)}"); st.dataframe(errs)

    t1, t2, t3, t4 = st.tabs(["🏭 Gantt Operativo", "📦 Rastreo", "🔬 Inspector", "📥 Exportar"])
    
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
