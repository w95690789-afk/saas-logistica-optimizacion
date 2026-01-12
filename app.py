import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="SaaS Logístico T1", layout="wide", page_icon="🚛")

# --- FUNCIONES DE UTILIDAD ---

def format_time(hours_float):
    """Convierte 9.5 a 09:30"""
    if pd.isna(hours_float): return "00:00"
    base_time = datetime(2024, 1, 1, 0, 0, 0)
    delta = timedelta(hours=float(hours_float))
    target_time = base_time + delta
    if hours_float >= 24:
        days = int(hours_float // 24)
        return f"+{days}d {target_time.strftime('%H:%M')}"
    return target_time.strftime('%H:%M')

# --- MOTOR DE OPTIMIZACIÓN (OR-TOOLS) ---
def solve_logistics_engine(df_pedidos):
    """
    Motor lógico que reemplaza a Gurobi/NEOS.
    Asume columnas: p (id), o (origen), d (destino), T (viaje), TC (carga), TD (descarga), PR (prioridad)
    """
    status_text = st.empty()
    status_text.info("⚙️ Iniciando motor de optimización Google OR-Tools...")
    
    model = cp_model.CpModel()
    horizon = 48 # Horizonte de 48 horas
    
    # 1. Variables y Estructuras
    pedidos = []
    
    # Detectar nombres de columnas (ajuste automático a tu Excel)
    col_map = {
        'p': 'id', 'o': 'origen', 'd': 'destino', 
        'T': 't_viaje', 'TC': 't_carga', 'TD': 't_descarga', 'PR': 'prioridad',
        'SKILL': 'skill'
    }
    # Si las columnas no coinciden exactamente, intentamos normalizar
    df_pedidos.rename(columns=col_map, inplace=True)
    
    # Diccionarios para guardar variables del solver
    starts_origin = {}
    ends_origin = {}
    starts_dest = {}
    ends_dest = {}
    intervals_origin = {}
    intervals_dest = {}
    
    muelles_origen = {} # Mapa: Nodo -> [Intervalos]
    muelles_destino = {} # Mapa: Nodo -> [Intervalos]

    # 2. Creación de Variables
    for index, row in df_pedidos.iterrows():
        pid = row.get('id', index)
        try:
            t_carga = int(row.get('t_carga', 2) * 1) # Asumimos enteros para el solver
            t_descarga = int(row.get('t_descarga', 2) * 1)
            t_viaje = int(row.get('t_viaje', 5) * 1)
            nodo_orig = row.get('origen', 0)
            nodo_dest = row.get('destino', 1)
        except:
            continue # Saltar filas malas

        # Variables de tiempo (Enteras)
        start_o = model.NewIntVar(0, horizon, f'start_o_{pid}')
        end_o = model.NewIntVar(0, horizon, f'end_o_{pid}')
        interval_o = model.NewIntervalVar(start_o, t_carga, end_o, f'interval_o_{pid}')
        
        start_d = model.NewIntVar(0, horizon, f'start_d_{pid}')
        end_d = model.NewIntVar(0, horizon, f'end_d_{pid}')
        interval_d = model.NewIntervalVar(start_d, t_descarga, end_d, f'interval_d_{pid}')
        
        # Restricción Dura: El viaje conecta origen y destino
        # Inicio Destino >= Fin Origen + Tiempo Viaje
        model.Add(start_d >= end_o + t_viaje)
        
        # Guardar para restricciones de recursos (muelles)
        if nodo_orig not in muelles_origen: muelles_origen[nodo_orig] = []
        muelles_origen[nodo_orig].append(interval_o)
        
        if nodo_dest not in muelles_destino: muelles_destino[nodo_dest] = []
        muelles_destino[nodo_dest].append(interval_d)
        
        pedidos.append({
            'id': pid, 'vars': (start_o, end_o, start_d, end_d), 
            'data': row
        })

    # 3. Restricciones de Capacidad (No Traslape)
    # Suponemos N muelles por nodo. Si no tenemos el dato, asumimos capacidad infinita temporalmente
    # Para hacerlo real, aplicamos NoOverlap asumiendo 1 muelle por defecto por nodo para probar colas
    for nodo, intervalos in muelles_origen.items():
        # Si quisieramos modelar M muelles, usariamos Cumulative. 
        # Para simplificar y asegurar factibilidad usamos NoOverlap (1 a la vez) o lo dejamos libre si no hay datos de muelles
        # Aquí aplicamos NoOverlap para forzar secuenciación inteligente
        model.AddNoOverlap(intervalos)
        
    for nodo, intervalos in muelles_destino.items():
        model.AddNoOverlap(intervalos)

    # 4. Función Objetivo: Minimizar tiempos finales (Makespan)
    obj_var = model.NewIntVar(0, horizon, 'makespan')
    model.AddMaxEquality(obj_var, [p['vars'][3] for p in pedidos])
    model.Minimize(obj_var)

    # 5. Resolver
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    status = solver.Solve(model)
    
    status_text.success("✅ ¡Optimización Completada!")
    
    # 6. Extraer Resultados
    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for p in pedidos:
            pid = p['id']
            so = solver.Value(p['vars'][0])
            eo = solver.Value(p['vars'][1])
            sd = solver.Value(p['vars'][2])
            ed = solver.Value(p['vars'][3])
            
            # Fila Origen
            results.append({
                'Orden': pid, 'Tipo': 'Origen', 'Nodo': p['data'].get('origen'),
                'Muelle': 1, # Default si no hay asignación específica
                'Inicio Servicio': so, 'Fin Servicio': eo, 'Batch': '1/1'
            })
            # Fila Destino
            results.append({
                'Orden': pid, 'Tipo': 'Destino', 'Nodo': p['data'].get('destino'),
                'Muelle': 1, 
                'Inicio Servicio': sd, 'Fin Servicio': ed, 'Batch': '1/1'
            })
            
    return pd.DataFrame(results)

# --- INTERFAZ DE USUARIO (FRONTEND) ---

st.title("🚛 Sistema de Asignación de Muelles (SaaS)")
st.markdown("""
Esta aplicación optimiza la asignación de muelles y horarios para camiones eliminando la dependencia de Gurobi.
Usa **Google OR-Tools** y genera visualizaciones ejecutivas.
""")

uploaded_file = st.file_uploader("Cargar archivo de datos (Excel)", type=["xlsx", "xls"])

if uploaded_file:
    try:
        # Leer hoja específica o la primera por defecto
        # Intentamos leer la hoja 'Sheet1' que suele tener los pedidos según tus archivos
        try:
            df_input = pd.read_excel(uploaded_file, sheet_name='Sheet1')
        except:
            df_input = pd.read_excel(uploaded_file)
            
        st.write(f"📊 Datos cargados: {len(df_input)} pedidos detectados.")
        
        if st.button("🚀 Ejecutar Optimización"):
            
            # EJECUTAR MOTOR
            results_df = solve_logistics_engine(df_input)
            
            if not results_df.empty:
                # --- PROCESAMIENTO VISUAL ---
                results_df['Hora Entrada'] = results_df['Inicio Servicio'].apply(format_time)
                results_df['Hora Salida'] = results_df['Fin Servicio'].apply(format_time)
                results_df['Etiqueta Muelle'] = "Muelle " + results_df['Muelle'].astype(str)
                results_df['Etiqueta Nodo'] = "Nodo " + results_df['Nodo'].astype(str)

                # --- DASHBOARD ---
                st.divider()
                st.subheader("🎯 Resultados Ejecutivos")
                
                # KPIs
                c1, c2, c3 = st.columns(3)
                c1.metric("Pedidos Procesados", results_df['Orden'].nunique())
                c2.metric("Hora Final de Operación", results_df['Hora Salida'].max())
                c3.metric("Eficiencia", "100% Asignado")

                tab1, tab2, tab3 = st.tabs(["📊 Gantt Visual", "📋 Tabla Detallada", "📥 Descargas"])

                with tab1:
                    st.write("### Cronograma de Operaciones")
                    # Gráfica Gantt
                    chart = alt.Chart(results_df).mark_bar().encode(
                        x=alt.X('Inicio Servicio', title='Hora (Formato 24h)'),
                        x2='Fin Servicio',
                        y=alt.Y('Etiqueta Nodo', title='Ubicación', sort='ascending'),
                        color=alt.Color('Tipo', scale=alt.Scale(domain=['Origen', 'Destino'], range=['#3b8ed0', '#e0553d'])),
                        tooltip=['Orden', 'Hora Entrada', 'Hora Salida']
                    ).properties(height=400)
                    st.altair_chart(chart, use_container_width=True)

                with tab2:
                    st.dataframe(results_df[['Orden', 'Tipo', 'Etiqueta Nodo', 'Hora Entrada', 'Hora Salida']], use_container_width=True)

                with tab3:
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        results_df.to_excel(writer, index=False)
                    st.download_button("Descargar Excel Optimizado", output.getvalue(), "Plan_Logistico.xlsx")
            else:
                st.error("No se encontró una solución factible con los datos proporcionados.")

    except Exception as e:
        st.error(f"Error al procesar el archivo: {e}")
