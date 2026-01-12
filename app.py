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
    """
    Revisa matemáticamente si hay solapamientos en algún muelle.
    Retorna: (Status Booleano, DataFrame de Errores)
    """
    errors = []
    # Agrupar por Nodo y Muelle para revisar carril por carril
    grouped = df.groupby(['Nodo', 'Muelle'])
    
    for (nodo, muelle), group in grouped:
        # Ordenar por hora de inicio
        group = group.sort_values('Inicio Servicio')
        last_end = -1
        last_order = None
        
        for idx, row in group.iterrows():
            start = row['Inicio Servicio']
            end = row['Fin Servicio']
            order = row['Orden']
            
            # Tolerancia de 0.001 para errores de punto flotante
            if start < (last_end - 0.001):
                errors.append({
                    'Nodo': nodo,
                    'Muelle': muelle,
                    'Conflicto': f"Pedido {last_order} vs {order}",
                    'Detalle': f"Fin A: {format_time(last_end)} > Inicio B: {format_time(start)}"
                })
            
            last_end = end
            last_order = order
            
    if len(errors) > 0:
        return False, pd.DataFrame(errors)
    return True, pd.DataFrame()

# --- MOTOR DE OPTIMIZACIÓN (OR-TOOLS) ---
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
    
    # UNIFICACIÓN DE RECURSOS (Anti-Colisión)
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
        
        # METER CARGAS Y DESCARGAS EN LA MISMA BOLSA
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
        status_text.error("⚠️ Saturación: No es posible agendar sin choques. Aumenta el horizonte de tiempo.")
        return pd.DataFrame()

# --- INTERFAZ DE USUARIO ---

st.title("🚛 Sistema de Asignación de Muelles (SaaS)")
st.markdown("Optimización logística inteligente con Google OR-Tools.")

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
                st.session_state['results_df'] = results_df
        
        if st.session_state['results_df'] is not None:
            results_df = st.session_state['results_df']
            
            st.divider()
            
            # --- SECCIÓN DE AUDITORÍA AUTOMÁTICA ---
            is_valid, error_df = audit_schedule(results_df)
            
            if is_valid:
                st.success("✅ AUDITORÍA APROBADA: El algoritmo verificó que NO existen solapamientos en ningún muelle.")
            else:
                st.error(f"❌ ALERTA DE COLISIÓN: Se detectaron {len(error_df)} conflictos de horario.")
                st.dataframe(error_df)
            
            # ---------------------------------------

            st.subheader("🎯 Dashboard de Operaciones")
            
            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("Total Pedidos", results_df['Orden'].nunique())
            kpi2.metric("Nodos Activos", results_df['Nodo'].nunique())
            kpi3.metric("Última Entrega", results_df['Hora Salida'].max())

            tab1, tab2, tab3, tab4 = st.tabs(["🌍 Visión Global", "🔬 Inspector de Nodos", "📋 Tabla Datos", "📥 Exportar"])

            with tab1:
                st.caption("Eje Y: Nodo | Eje X: Tiempo. Altura dinámica automática.")
                n_nodos = results_df['Nodo'].nunique()
                chart_height = max(400, n_nodos * 30)
                
                chart_global = alt.Chart(results_df).mark_bar(opacity=0.7).encode(
                    x=alt.X('Inicio Servicio', title='Hora Operativa'),
                    x2='Fin Servicio',
                    y=alt.Y('Etiqueta Nodo', sort='ascending', title='Ubicación'),
                    color=alt.Color('Tipo', scale=alt.Scale(domain=['Origen', 'Destino'], range=['#3b8ed0', '#e0553d'])),
                    tooltip=['Orden', 'Hora Entrada', 'Hora Salida', 'Etiqueta Nodo']
                ).properties(height=chart_height).interactive()
                st.altair_chart(chart_global, use_container_width=True)

            with tab2:
                # INTEGRACIÓN DEL STATUS EN LA PESTAÑA INSPECTOR TAMBIÉN
                st.markdown("### 🔎 Auditoría de Nodos")
                
                if not is_valid:
                    st.warning("⚠️ Atención: Revisa los nodos con conflictos listados arriba.")

                col_filt, col_info = st.columns([1, 3])
                with col_filt:
                    lista_nodos = sorted(results_df['Nodo'].unique())
                    nodo_sel = st.selectbox("Seleccionar Nodo a Auditar:", lista_nodos)
                
                df_nodo = results_df[results_df['Nodo'] == nodo_sel].copy()
                
                with col_info:
                    kpi_n1, kpi_n2 = st.columns(2)
                    kpi_n1.metric(f"Operaciones en Nodo {nodo_sel}", len(df_nodo))
                    ocupacion_h = (df_nodo['Fin Servicio'] - df_nodo['Inicio Servicio']).sum()
                    kpi_n2.metric("Horas Totales Ocupadas", f"{ocupacion_h:.1f} hrs")

                st.markdown("#### Cronograma Detallado del Nodo")
                
                chart_nodo = alt.Chart(df_nodo).mark_bar().encode(
                    x=alt.X('Inicio Servicio', title='Horas'),
                    x2='Fin Servicio',
                    y=alt.Y('Etiqueta Muelle', title='Carril'),
                    color='Tipo',
                    tooltip=['Orden', 'Hora Entrada', 'Hora Salida', 'Tipo']
                ).properties(height=300)
                st.altair_chart(chart_nodo, use_container_width=True)
                
                st.dataframe(df_nodo.sort_values('Inicio Servicio')[['Orden', 'Tipo', 'Hora Entrada', 'Hora Salida', 'Etiqueta Muelle']], use_container_width=True)

            with tab3:
                st.dataframe(results_df, use_container_width=True)

            with tab4:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    results_df.to_excel(writer, index=False)
                st.download_button("Descargar Excel Maestro", output.getvalue(), "Plan_Logistico_Full.xlsx")
    else:
        st.error("Error leyendo el archivo.")
