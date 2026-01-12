import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
import math
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SaaS Logístico T1", layout="wide", page_icon="🚛")

# --- ESTADO ---
if 'results_df' not in st.session_state:
    st.session_state['results_df'] = None

# --- FUNCIONES ---
def format_time(hours_float):
    if pd.isna(hours_float): return "00:00"
    base_time = datetime(2024, 1, 1, 0, 0, 0)
    delta = timedelta(hours=float(hours_float))
    target_time = base_time + delta
    if hours_float >= 24:
        days = int(hours_float // 24)
        return f"+{days}d {target_time.strftime('%H:%M')}"
    return target_time.strftime('%H:%M')

def generate_template():
    """Genera un archivo Excel vacío con los encabezados correctos"""
    df_template = pd.DataFrame(columns=[
        'ID Pedido', 'Nodo Origen', 'Nodo Destino', 
        'Tiempo Viaje (h)', 'Tiempo Carga (h)', 'Tiempo Descarga (h)', 
        'Prioridad', 'Tipo Vehículo'
    ])
    # Agregar una fila de ejemplo
    df_template.loc[0] = ['PED-001', 10, 50, 5.5, 2, 1.5, 1, 'Seco']
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_template.to_excel(writer, index=False, sheet_name='Plantilla_Carga')
        # Ajustar ancho de columnas
        worksheet = writer.sheets['Plantilla_Carga']
        for i, col in enumerate(df_template.columns):
            worksheet.set_column(i, i, 20)
    return output.getvalue()

def smart_load(file):
    try:
        xl = pd.ExcelFile(file)
        # Buscar hoja con datos
        target_sheet = None
        if 'Plantilla_Carga' in xl.sheet_names: target_sheet = 'Plantilla_Carga'
        elif 'Sheet1' in xl.sheet_names: target_sheet = 'Sheet1'
        else: target_sheet = xl.sheet_names[0]
        
        return pd.read_excel(file, sheet_name=target_sheet)
    except Exception as e:
        st.error(f"Error leyendo el Excel: {e}")
        return pd.DataFrame()

def assign_real_docks(df):
    df_out = df.copy()
    df_out['Muelle'] = 1 
    for nodo in df_out['Nodo'].unique():
        mask = df_out['Nodo'] == nodo
        tasks = df_out[mask].sort_values('Inicio Servicio')
        docks_free_time = [] 
        assignments = {}
        for idx, row in tasks.iterrows():
            start, end = row['Inicio Servicio'], row['Fin Servicio']
            assigned_dock = -1
            for dock_id, free_time in enumerate(docks_free_time):
                if start >= free_time:
                    docks_free_time[dock_id] = end
                    assigned_dock = dock_id + 1
                    break
            if assigned_dock == -1:
                docks_free_time.append(end)
                assigned_dock = len(docks_free_time)
            assignments[idx] = assigned_dock
        df_out.loc[assignments.keys(), 'Muelle'] = list(assignments.values())
    return df_out

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
                    'Conflicto': f"{last_order} vs {row['Orden']}",
                    'Detalle': "Choque horario"
                })
            last_end = row['Fin Servicio']
            last_order = row['Orden']
    if len(errors) > 0: return False, pd.DataFrame(errors)
    return True, pd.DataFrame()

# --- MOTOR DE OPTIMIZACIÓN ---
def solve_logistics_engine(df_raw):
    status_text = st.empty()
    status_text.info("⚙️ Procesando archivo y calculando optimización...")
    
    df_pedidos = df_raw.copy()
    
    # --- NORMALIZACIÓN DE NOMBRES DE COLUMNA ---
    # Convertimos todo a minúsculas y quitamos espacios para ser flexibles
    df_pedidos.columns = df_pedidos.columns.str.strip()
    
    # Diccionario de traducción: Español Profesional -> Variable Matemática
    # Soporta tanto el formato nuevo como el viejo (por si acaso)
    col_map = {
        # Formato Nuevo (Profesional)
        'ID Pedido': 'id',
        'Nodo Origen': 'origen',
        'Nodo Destino': 'destino',
        'Tiempo Viaje (h)': 't_viaje',
        'Tiempo Carga (h)': 't_carga',
        'Tiempo Descarga (h)': 't_descarga',
        'Prioridad': 'prioridad',
        'Tipo Vehículo': 'skill',
        # Formato Viejo (Soporte Legacy)
        'p': 'id', 'o': 'origen', 'd': 'destino', 
        'T': 't_viaje', 'TC': 't_carga', 'TD': 't_descarga', 
        'PR': 'prioridad', 'SKILL': 'skill'
    }
    
    df_pedidos.rename(columns=col_map, inplace=True)
    
    required_cols = ['id', 'origen', 'destino', 't_viaje']
    missing = [c for c in required_cols if c not in df_pedidos.columns]
    
    if missing:
        status_text.error(f"❌ Error de Formato: No encontramos las columnas clave. Faltan: {missing}. Por favor descarga la plantilla oficial.")
        return pd.DataFrame()

    # Relleno de valores por defecto si faltan opcionales
    if 't_carga' not in df_pedidos: df_pedidos['t_carga'] = 2
    if 't_descarga' not in df_pedidos: df_pedidos['t_descarga'] = 2

    model = cp_model.CpModel()
    horizon = 96 
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

    # Capacidad
    CAPACIDAD_MUELLES = 5 
    for nodo, intervalos in recursos_nodo.items():
        demands = [1] * len(intervalos)
        model.AddCumulative(intervalos, demands, CAPACIDAD_MUELLES)

    if pedidos:
        obj_var = model.NewIntVar(0, horizon, 'makespan')
        model.AddMaxEquality(obj_var, [p['vars'][3] for p in pedidos])
        model.Minimize(obj_var)
    else: return pd.DataFrame()

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60
    status = solver.Solve(model)
    
    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        status_text.success("✅ Optimización Exitosa. Generando reportes...")
        for p in pedidos:
            pid = p['id']
            so, eo, sd, ed = solver.Value(p['vars'][0]), solver.Value(p['vars'][1]), solver.Value(p['vars'][2]), solver.Value(p['vars'][3])
            results.append({'Orden': pid, 'Tipo': 'Origen', 'Nodo': p['data'].get('origen'), 'Inicio Servicio': so, 'Fin Servicio': eo})
            results.append({'Orden': pid, 'Tipo': 'Destino', 'Nodo': p['data'].get('destino'), 'Inicio Servicio': sd, 'Fin Servicio': ed})
        
        return assign_real_docks(pd.DataFrame(results))
    else:
        status_text.error("⚠️ No se encontró solución factible.")
        return pd.DataFrame()

# --- UI ---

with st.sidebar:
    st.header("Herramientas")
    st.markdown("¿No tienes el archivo?")
    plantilla_data = generate_template()
    st.download_button(
        label="📄 Descargar Plantilla Excel",
        data=plantilla_data,
        file_name="Plantilla_Optimizacion_Logistica.xlsx",
        mime="application/vnd.ms-excel",
        help="Descarga este archivo, llénalo con tus pedidos y súbelo en el panel principal."
    )
    st.divider()
    st.info("SaaS v9.0 - Motor T1")

st.title("🚛 SaaS Logístico T1")
st.markdown("### Plataforma de Optimización de Muelles")

uploaded_file = st.file_uploader("Sube tu archivo de pedidos (Usa la plantilla del menú lateral)", type=["xlsx", "xls"])

if uploaded_file:
    df_input = smart_load(uploaded_file)
    if not df_input.empty:
        st.write(f"📊 Archivo cargado: {len(df_input)} registros encontrados.")
        
        # Validación rápida de columnas visibles
        st.dataframe(df_input.head(3), use_container_width=True)
        
        if st.button("🚀 Optimizar Red Logística"):
            results_df = solve_logistics_engine(df_input)
            if not results_df.empty:
                results_df['Hora Entrada'] = results_df['Inicio Servicio'].apply(format_time)
                results_df['Hora Salida'] = results_df['Fin Servicio'].apply(format_time)
                results_df['Etiqueta Nodo'] = "Nodo " + results_df['Nodo'].astype(str)
                results_df['Etiqueta Muelle'] = "Muelle " + results_df['Muelle'].astype(str)
                results_df['Etiqueta Pedido'] = "Pedido #" + results_df['Orden'].astype(str)
                st.session_state['results_df'] = results_df
        
        if st.session_state['results_df'] is not None:
            results_df = st.session_state['results_df']
            st.divider()
            
            is_valid, error_df = audit_schedule(results_df)
            if is_valid: st.success("✅ AUDITORÍA DE CALIDAD: APROBADA (0 Conflictos)")
            else: st.error(f"❌ ERROR CRÍTICO: {len(error_df)} choques de muelle detectados.")

            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("Pedidos Programados", results_df['Orden'].nunique())
            kpi2.metric("Centros Activos", results_df['Nodo'].nunique())
            kpi3.metric("Lead Time Total", f"{results_df['Fin Servicio'].max()} h")

            tabs = st.tabs(["📊 Capacidad & Saturación", "📦 Rastreo Pedidos", "🏭 Gantt Patio", "🔬 Inspector", "📥 Reportes"])

            # 1. CURVAS DE CAPACIDAD
            with tabs[0]:
                st.markdown("### 📈 Monitor de Ocupación")
                occupancy_data = []
                for _, row in results_df.iterrows():
                    start_h = int(math.floor(row['Inicio Servicio']))
                    end_h = int(math.ceil(row['Fin Servicio']))
                    if end_h == start_h: end_h += 1
                    for h in range(start_h, end_h):
                        occupancy_data.append({'Etiqueta Nodo': row['Etiqueta Nodo'], 'Hora': h, 'Camiones': 1})
                
                if occupancy_data:
                    df_occ = pd.DataFrame(occupancy_data).groupby(['Etiqueta Nodo', 'Hora']).size().reset_index(name='Total Camiones')
                    
                    mode_view = st.radio("Visualización:", ["Matriz de Calor (Números)", "Curva de Carga"], horizontal=True)
                    
                    if mode_view == "Curva de Carga":
                        sel_nodo_curve = st.selectbox("Ver Nodo:", sorted(df_occ['Etiqueta Nodo'].unique()))
                        df_curve = df_occ[df_occ['Etiqueta Nodo'] == sel_nodo_curve]
                        base = alt.Chart(df_curve).encode(x=alt.X('Hora', title='Hora Operativa'))
                        area = base.mark_area(opacity=0.6, color='#3b8ed0').encode(y='Total Camiones')
                        line = base.mark_line(color='#3b8ed0').encode(y='Total Camiones')
                        rule = alt.Chart(pd.DataFrame({'y': [5]})).mark_rule(color='red', strokeDash=[5,5]).encode(y='y')
                        st.altair_chart((area + line + rule).properties(height=300), use_container_width=True)
                    else:
                        base_rect = alt.Chart(df_occ).mark_rect().encode(
                            x=alt.X('Hora:O', title='Hora'), y=alt.Y('Etiqueta Nodo'),
                            color=alt.Color('Total Camiones', scale=alt.Scale(scheme='blues'), legend=None)
                        )
                        text_labels = alt.Chart(df_occ).mark_text(baseline='middle').encode(
                            x='Hora:O', y='Etiqueta Nodo', text='Total Camiones', color=alt.value('black')
                        )
                        st.altair_chart((base_rect + text_labels).properties(height=max(500, df_occ['Etiqueta Nodo'].nunique()*30)), use_container_width=True)

            # 2. RASTREO
            with tabs[1]:
                st.markdown("### 🚛 Trazabilidad")
                sel_pedidos = st.multiselect("Buscar Pedido:", sorted(results_df['Orden'].unique()))
                df_view = results_df if not sel_pedidos else results_df[results_df['Orden'].isin(sel_pedidos)]
                chart_ped = alt.Chart(df_view).mark_bar().encode(
                    x=alt.X('Inicio Servicio', title='Horas'), x2='Fin Servicio',
                    y=alt.Y('Etiqueta Pedido', sort='ascending'), color='Tipo',
                    tooltip=['Orden', 'Etiqueta Nodo', 'Hora Entrada', 'Hora Salida']
                ).properties(height=max(400, df_view['Orden'].nunique()*20)).interactive()
                st.altair_chart(chart_ped, use_container_width=True)

            # 3. GANTT
            with tabs[2]:
                st.markdown("### 🏭 Vista de Patio")
                chart_global = alt.Chart(results_df).mark_bar(opacity=0.7).encode(
                    x='Inicio Servicio', x2='Fin Servicio',
                    y=alt.Y('Etiqueta Nodo', sort='ascending'), color='Tipo',
                    tooltip=['Orden', 'Hora Entrada', 'Etiqueta Muelle']
                ).properties(height=max(400, results_df['Nodo'].nunique()*30)).interactive()
                st.altair_chart(chart_global, use_container_width=True)

            # 4. INSPECTOR
            with tabs[3]:
                st.markdown("### 🔎 Detalle por Muelle")
                n_sel = st.selectbox("Nodo:", sorted(results_df['Nodo'].unique()))
                df_n = results_df[results_df['Nodo'] == n_sel]
                chart_n = alt.Chart(df_n).mark_bar().encode(
                    x='Inicio Servicio', x2='Fin Servicio', y='Etiqueta Muelle', color='Tipo',
                    tooltip=['Orden', 'Hora Entrada']
                ).properties(height=300)
                st.altair_chart(chart_n, use_container_width=True)
                st.dataframe(df_n.sort_values('Inicio Servicio')[['Orden','Tipo','Hora Entrada','Etiqueta Muelle']], use_container_width=True)

            # 5. EXPORTAR
            with tabs[4]:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    results_df.to_excel(writer, index=False)
                st.download_button("Descargar Resultados (.xlsx)", output.getvalue(), "Plan_Logistico.xlsx")
    else:
        st.error("Error leyendo archivo.")
