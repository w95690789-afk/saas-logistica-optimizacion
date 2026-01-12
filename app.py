import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="SaaS Logístico T1", layout="wide", page_icon="🚛")

# --- GESTIÓN DE ESTADO ---
if 'results_df' not in st.session_state:
    st.session_state['results_df'] = None

# --- FUNCIONES DE UTILIDAD ---
def format_time(hours_float):
    if pd.isna(hours_float): return "00:00"
    base_time = datetime(2024, 1, 1, 0, 0, 0)
    delta = timedelta(hours=float(hours_float))
    target_time = base_time + delta
    if hours_float >= 24:
        days = int(hours_float // 24)
        return f"+{days}d {target_time.strftime('%H:%M')}"
    return target_time.strftime('%H:%M')

def smart_load(file):
    try:
        xl = pd.ExcelFile(file)
        if 'Sheet1' in xl.sheet_names:
            return pd.read_excel(file, sheet_name='Sheet1')
        for sheet in xl.sheet_names:
            df = pd.read_excel(file, sheet_name=sheet)
            if {'p', 'o', 'd'}.issubset(df.columns):
                return df
        if len(xl.sheet_names) > 1:
            return pd.read_excel(file, sheet_name=1)
        return pd.read_excel(file)
    except Exception as e:
        st.error(f"Error leyendo el Excel: {e}")
        return pd.DataFrame()

def audit_schedule(df):
    errors = []
    grouped = df.groupby(['Nodo', 'Muelle'])
    for (nodo, muelle), group in grouped:
        group = group.sort_values('Inicio Servicio')
        last_end = -1
        last_order = None
        for idx, row in group.iterrows():
            start = row['Inicio Servicio']
            if start < (last_end - 0.001):
                errors.append({
                    'Nodo': nodo, 'Muelle': muelle,
                    'Conflicto': f"Pedido {last_order} vs {order}",
                    'Detalle': f"Choque de horarios"
                })
            last_end = row['Fin Servicio']
            last_order = row['Orden']
    if len(errors) > 0: return False, pd.DataFrame(errors)
    return True, pd.DataFrame()

# --- MOTOR DE OPTIMIZACIÓN ---
def solve_logistics_engine(df_raw):
    status_text = st.empty()
    status_text.info("⚙️ Iniciando motor de optimización Google OR-Tools...")
    
    df_pedidos = df_raw.copy()
    col_map = {
        'p': 'id', 'o': 'origen', 'd': 'destino', 
        'T': 't_viaje', 'TC': 't_carga', 'TD': 't_descarga', 'PR': 'prioridad',
        'SKILL': 'skill'
    }
    df_pedidos.rename(columns=col_map, inplace=True)
    
    required_cols = ['id', 'origen', 'destino', 't_viaje']
    missing = [c for c in required_cols if c not in df_pedidos.columns]
    if missing:
        status_text.error(f"❌ Faltan columnas: {missing}")
        return pd.DataFrame()

    model = cp_model.CpModel()
    horizon = 72 
    pedidos = []
    recursos_nodo = {} 

    for index, row in df_pedidos.iterrows():
        try:
            pid = row.get('id', index)
            t_carga = int(float(row.get('t_carga', 2)))
            t_descarga = int(float(row.get('t_descarga', 2)))
            t_viaje = int(float(row.get('t_viaje', 5)))
            nodo_orig = row.get('origen')
            nodo_dest = row.get('destino')
            if pd.isna(nodo_orig) or pd.isna(nodo_dest): continue
        except: continue

        start_o = model.NewIntVar(0, horizon, f'start_o_{pid}')
        end_o = model.NewIntVar(0, horizon, f'end_o_{pid}')
        interval_o = model.NewIntervalVar(start_o, t_carga, end_o, f'interval_o_{pid}')
        
        start_d = model.NewIntVar(0, horizon, f'start_d_{pid}')
        end_d = model.NewIntVar(0, horizon, f'end_d_{pid}')
        interval_d = model.NewIntervalVar(start_d, t_descarga, end_d, f'interval_d_{pid}')
        
        model.Add(start_d >= end_o + t_viaje)
        
        if nodo_orig not in recursos_nodo: recursos_nodo[nodo_orig] = []
        recursos_nodo[nodo_orig].append(interval_o)
        if nodo_dest not in recursos_nodo: recursos_nodo[nodo_dest] = []
        recursos_nodo[nodo_dest].append(interval_d)
        
        pedidos.append({'id': pid, 'vars': (start_o, end_o, start_d, end_d), 'data': row})

    for nodo, intervalos in recursos_nodo.items():
        model.AddNoOverlap(intervalos)

    if pedidos:
        obj_var = model.NewIntVar(0, horizon, 'makespan')
        model.AddMaxEquality(obj_var, [p['vars'][3] for p in pedidos])
        model.Minimize(obj_var)
    else: return pd.DataFrame()

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 45
    status = solver.Solve(model)
    
    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        status_text.success("✅ ¡Optimización Completada!")
        for p in pedidos:
            pid = p['id']
            so, eo, sd, ed = solver.Value(p['vars'][0]), solver.Value(p['vars'][1]), solver.Value(p['vars'][2]), solver.Value(p['vars'][3])
            
            results.append({'Orden': pid, 'Tipo': 'Origen', 'Nodo': p['data'].get('origen'), 'Muelle': 1, 'Inicio Servicio': so, 'Fin Servicio': eo, 'Batch': '1/1'})
            results.append({'Orden': pid, 'Tipo': 'Destino', 'Nodo': p['data'].get('destino'), 'Muelle': 1, 'Inicio Servicio': sd, 'Fin Servicio': ed, 'Batch': '1/1'})
        return pd.DataFrame(results)
    else:
        status_text.error("⚠️ Saturación: Aumenta el horizonte de tiempo.")
        return pd.DataFrame()

# --- INTERFAZ DE USUARIO ---

st.title("🚛 SaaS Logístico T1")

uploaded_file = st.file_uploader("Cargar archivo de datos (Excel)", type=["xlsx", "xls"])

if uploaded_file:
    df_input = smart_load(uploaded_file)
    if not df_input.empty:
        st.write(f"📊 Datos cargados: {len(df_input)} filas.")
        
        if st.button("🚀 Ejecutar Optimización"):
            results_df = solve_logistics_engine(df_input)
            if not results_df.empty:
                results_df['Hora Entrada'] = results_df['Inicio Servicio'].apply(format_time)
                results_df['Hora Salida'] = results_df['Fin Servicio'].apply(format_time)
                results_df['Etiqueta Nodo'] = "Nodo " + results_df['Nodo'].astype(str)
                results_df['Etiqueta Muelle'] = "Muelle " + results_df['Muelle'].astype(str)
                # Crear columna 'Etiqueta Pedido' para el eje Y
                results_df['Etiqueta Pedido'] = "Pedido #" + results_df['Orden'].astype(str)
                st.session_state['results_df'] = results_df
        
        if st.session_state['results_df'] is not None:
            results_df = st.session_state['results_df']
            
            st.divider()
            is_valid, error_df = audit_schedule(results_df)
            if is_valid: st.success("✅ AUDITORÍA APROBADA: 0 Colisiones.")
            else: st.error(f"❌ ALERTA: {len(error_df)} Conflictos detectados.")

            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("Pedidos", results_df['Orden'].nunique())
            kpi2.metric("Nodos", results_df['Nodo'].nunique())
            kpi3.metric("Makespan (Horas)", results_df['Fin Servicio'].max())

            # --- PESTAÑAS PRINCIPALES ---
            tab_nodos, tab_pedidos, tab_calor, tab_inspector, tab_datos = st.tabs([
                "🏭 Gantt Nodos", 
                "📦 Rastreo Pedidos (Nuevo)", 
                "🔥 Mapa de Calor",
                "🔬 Inspector", 
                "📥 Exportar"
            ])

            # 1. GANTT POR NODOS (Visión de Patio)
            with tab_nodos:
                st.caption("Planificación desde la perspectiva del Almacén.")
                h_nodos = max(400, results_df['Nodo'].nunique() * 30)
                chart_nodos = alt.Chart(results_df).mark_bar(opacity=0.7).encode(
                    x=alt.X('Inicio Servicio', title='Hora'),
                    x2='Fin Servicio',
                    y=alt.Y('Etiqueta Nodo', sort='ascending'),
                    color='Tipo',
                    tooltip=['Orden', 'Hora Entrada']
                ).properties(height=h_nodos).interactive()
                st.altair_chart(chart_nodos, use_container_width=True)

            # 2. GANTT POR PEDIDOS (Visión de Tráfico) - NUEVO
            with tab_pedidos:
                st.markdown("### 🚛 Ciclo de Vida del Pedido")
                st.caption("Aquí puedes ver el viaje completo: La barra azul es la carga, el espacio vacío es el viaje, y la barra roja es la descarga.")
                
                # Filtro opcional
                pedidos_list = sorted(results_df['Orden'].unique())
                sel_pedidos = st.multiselect("Filtrar Pedidos Específicos (Dejar vacío para ver todos)", pedidos_list)
                
                df_view_ped = results_df if not sel_pedidos else results_df[results_df['Orden'].isin(sel_pedidos)]
                
                # Altura dinámica
                h_pedidos = max(400, df_view_ped['Orden'].nunique() * 25)
                
                chart_pedidos = alt.Chart(df_view_ped).mark_bar().encode(
                    x=alt.X('Inicio Servicio', title='Línea de Tiempo (Horas)'),
                    x2='Fin Servicio',
                    y=alt.Y('Etiqueta Pedido', sort='ascending', title='ID Pedido'),
                    color=alt.Color('Tipo', scale=alt.Scale(range=['#3b8ed0', '#e0553d'])), # Azul y Rojo
                    tooltip=['Orden', 'Etiqueta Nodo', 'Hora Entrada', 'Hora Salida']
                ).properties(height=h_pedidos).interactive()
                
                st.altair_chart(chart_pedidos, use_container_width=True)

            # 3. MAPA DE CALOR (Visión de Capacidad) - NUEVO
            with tab_calor:
                st.markdown("### 🔥 Zonas de Alta Congestión")
                st.caption("Muestra cuántos camiones hay simultáneamente en cada nodo por hora.")
                
                # Preparamos datos para heatmap (contar ocurrencias por hora)
                # Simplificación: Tomamos la hora de inicio truncada
                df_heat = results_df.copy()
                df_heat['Hora_Simple'] = df_heat['Inicio Servicio'].astype(int)
                heat_data = df_heat.groupby(['Etiqueta Nodo', 'Hora_Simple']).size().reset_index(name='Camiones')
                
                chart_heat = alt.Chart(heat_data).mark_rect().encode(
                    x=alt.X('Hora_Simple:O', title='Hora del Día'),
                    y=alt.Y('Etiqueta Nodo', title='Nodo'),
                    color=alt.Color('Camiones', scale=alt.Scale(scheme='orangered')),
                    tooltip=['Etiqueta Nodo', 'Hora_Simple', 'Camiones']
                ).properties(height=500)
                
                st.altair_chart(chart_heat, use_container_width=True)

            # 4. INSPECTOR (Auditoría)
            with tab_inspector:
                st.markdown("### 🔎 Lupa de Muelles")
                n_sel = st.selectbox("Seleccionar Nodo:", sorted(results_df['Nodo'].unique()))
                df_n = results_df[results_df['Nodo'] == n_sel]
                
                chart_n = alt.Chart(df_n).mark_bar().encode(
                    x='Inicio Servicio', x2='Fin Servicio',
                    y='Etiqueta Muelle', color='Tipo',
                    tooltip=['Orden', 'Hora Entrada']
                ).properties(height=300)
                st.altair_chart(chart_n, use_container_width=True)
                st.dataframe(df_n.sort_values('Inicio Servicio')[['Orden','Tipo','Hora Entrada','Etiqueta Muelle']], use_container_width=True)

            # 5. EXPORTAR
            with tab_datos:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    results_df.to_excel(writer, index=False)
                st.download_button("Descargar Excel Full", output.getvalue(), "Plan_Maestro.xlsx")

    else:
        st.error("Error leyendo el archivo.")
