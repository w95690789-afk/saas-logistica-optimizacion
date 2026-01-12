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

def smart_load(file):
    """Busca la hoja correcta ignorando 'Instrucciones'"""
    try:
        xl = pd.ExcelFile(file)
        
        # 1. Intento Directo: Buscar 'Sheet1' (donde están tus datos)
        if 'Sheet1' in xl.sheet_names:
            return pd.read_excel(file, sheet_name='Sheet1')
            
        # 2. Intento Inteligente: Buscar hoja con columnas clave ('p', 'o', 'd')
        for sheet in xl.sheet_names:
            df = pd.read_excel(file, sheet_name=sheet)
            # Verificamos si tiene las columnas minimas necesarias
            if {'p', 'o', 'd'}.issubset(df.columns):
                return df
                
        # 3. Último recurso: La segunda hoja (asumiendo que la 1ra es instrucciones)
        if len(xl.sheet_names) > 1:
            return pd.read_excel(file, sheet_name=1)
            
        return pd.read_excel(file) # Fallback total
    except Exception as e:
        st.error(f"Error leyendo el Excel: {e}")
        return pd.DataFrame()

# --- MOTOR DE OPTIMIZACIÓN (OR-TOOLS) ---
def solve_logistics_engine(df_raw):
    status_text = st.empty()
    status_text.info("⚙️ Iniciando motor de optimización Google OR-Tools...")
    
    # Pre-Validación de Columnas
    df_pedidos = df_raw.copy()
    
    # Mapa de columnas basado en tu archivo 'Sheet1' real
    # Tus columnas son: p, o, d, PE, PR, T, TC, TD, ...
    col_map = {
        'p': 'id', 'o': 'origen', 'd': 'destino', 
        'T': 't_viaje', 'TC': 't_carga', 'TD': 't_descarga', 'PR': 'prioridad',
        'SKILL': 'skill'
    }
    df_pedidos.rename(columns=col_map, inplace=True)
    
    # Verificar si el mapeo funcionó
    required_cols = ['id', 'origen', 'destino', 't_viaje']
    missing = [c for c in required_cols if c not in df_pedidos.columns]
    if missing:
        status_text.error(f"❌ Error: No se encontraron las columnas de datos. Faltan: {missing}. Revisar que la hoja 'Sheet1' tenga encabezados p, o, d, T.")
        return pd.DataFrame()

    model = cp_model.CpModel()
    horizon = 72 # Aumentamos horizonte a 72h para evitar Infeasible por tiempo
    
    # 1. Variables y Estructuras
    pedidos = []
    
    muelles_origen = {} 
    muelles_destino = {} 

    # 2. Creación de Variables
    for index, row in df_pedidos.iterrows():
        # Asegurar tipos de datos
        try:
            pid = row.get('id', index)
            t_carga = int(float(row.get('t_carga', 2)))
            t_descarga = int(float(row.get('t_descarga', 2)))
            t_viaje = int(float(row.get('t_viaje', 5)))
            nodo_orig = row.get('origen')
            nodo_dest = row.get('destino')
            
            # Validación simple de datos sucios
            if pd.isna(nodo_orig) or pd.isna(nodo_dest): continue
        except:
            continue

        # Variables de tiempo (Enteras)
        start_o = model.NewIntVar(0, horizon, f'start_o_{pid}')
        end_o = model.NewIntVar(0, horizon, f'end_o_{pid}')
        interval_o = model.NewIntervalVar(start_o, t_carga, end_o, f'interval_o_{pid}')
        
        start_d = model.NewIntVar(0, horizon, f'start_d_{pid}')
        end_d = model.NewIntVar(0, horizon, f'end_d_{pid}')
        interval_d = model.NewIntervalVar(start_d, t_descarga, end_d, f'interval_d_{pid}')
        
        # Restricción Dura: Viaje
        model.Add(start_d >= end_o + t_viaje)
        
        # Agrupar por nodo para gestionar colas
        if nodo_orig not in muelles_origen: muelles_origen[nodo_orig] = []
        muelles_origen[nodo_orig].append(interval_o)
        
        if nodo_dest not in muelles_destino: muelles_destino[nodo_dest] = []
        muelles_destino[nodo_dest].append(interval_d)
        
        pedidos.append({
            'id': pid, 'vars': (start_o, end_o, start_d, end_d), 
            'data': row
        })

    # 3. Restricciones de Capacidad (No Traslape)
    for nodo, intervalos in muelles_origen.items():
        model.AddNoOverlap(intervalos)
        
    for nodo, intervalos in muelles_destino.items():
        model.AddNoOverlap(intervalos)

    # 4. Función Objetivo: Minimizar makespan
    if pedidos:
        obj_var = model.NewIntVar(0, horizon, 'makespan')
        model.AddMaxEquality(obj_var, [p['vars'][3] for p in pedidos])
        model.Minimize(obj_var)
    else:
        status_text.warning("⚠️ No se encontraron pedidos válidos para procesar.")
        return pd.DataFrame()

    # 5. Resolver
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 45
    status = solver.Solve(model)
    
    # 6. Resultados
    results = []
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        status_text.success("✅ ¡Optimización Completada!")
        for p in pedidos:
            pid = p['id']
            # Extraer valores
            so = solver.Value(p['vars'][0])
            eo = solver.Value(p['vars'][1])
            sd = solver.Value(p['vars'][2])
            ed = solver.Value(p['vars'][3])
            
            # Fila Origen
            results.append({
                'Orden': pid, 'Tipo': 'Origen', 'Nodo': p['data'].get('origen'),
                'Muelle': 1, 'Inicio Servicio': so, 'Fin Servicio': eo, 'Batch': '1/1'
            })
            # Fila Destino
            results.append({
                'Orden': pid, 'Tipo': 'Destino', 'Nodo': p['data'].get('destino'),
                'Muelle': 1, 'Inicio Servicio': sd, 'Fin Servicio': ed, 'Batch': '1/1'
            })
        return pd.DataFrame(results)
    else:
        status_text.error("⚠️ No se encontró solución factible (Posible saturación de horario).")
        return pd.DataFrame()

# --- INTERFAZ DE USUARIO ---

st.title("🚛 Sistema de Asignación de Muelles (SaaS)")
st.markdown("Optimización logística inteligente con Google OR-Tools.")

uploaded_file = st.file_uploader("Cargar archivo de datos (Excel)", type=["xlsx", "xls"])

if uploaded_file:
    # USAMOS LA CARGA INTELIGENTE AQUÍ
    df_input = smart_load(uploaded_file)
    
    if not df_input.empty:
        st.write(f"📊 Datos cargados correctamente. {len(df_input)} filas detectadas.")
        
        # Mostramos una vista previa para asegurar que leímos bien
        st.dataframe(df_input.head(3), use_container_width=True)
        
        if st.button("🚀 Ejecutar Optimización"):
            results_df = solve_logistics_engine(df_input)
            
            if not results_df.empty:
                # Procesamiento Visual
                results_df['Hora Entrada'] = results_df['Inicio Servicio'].apply(format_time)
                results_df['Hora Salida'] = results_df['Fin Servicio'].apply(format_time)
                results_df['Etiqueta Nodo'] = "Nodo " + results_df['Nodo'].astype(str)

                st.divider()
                st.subheader("🎯 Resultados")
                
                tab1, tab2 = st.tabs(["📊 Gantt", "📥 Descargar"])
                
                with tab1:
                    chart = alt.Chart(results_df).mark_bar().encode(
                        x='Inicio Servicio', x2='Fin Servicio',
                        y='Etiqueta Nodo', color='Tipo',
                        tooltip=['Orden', 'Hora Entrada']
                    ).properties(height=400)
                    st.altair_chart(chart, use_container_width=True)
                
                with tab2:
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        results_df.to_excel(writer, index=False)
                    st.download_button("Descargar Excel", output.getvalue(), "Plan.xlsx")
    else:
        st.error("No se pudieron leer datos del archivo.")
