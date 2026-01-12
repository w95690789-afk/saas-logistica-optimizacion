import streamlit as st
import pandas as pd
import numpy as np
import ast
import time
from ortools.sat.python import cp_model
import io

# Configuración de la página
st.set_page_config(page_title="Optimizador Logístico SaaS", layout="wide")

def load_data(uploaded_file):
    """Carga y procesa los datos del Excel."""
    try:
        xls = pd.ExcelFile(uploaded_file)
        df1 = pd.read_excel(xls, 'Sheet1')  # Pedidos
        df2 = pd.read_excel(xls, 'Sheet2')  # Ventanas de tiempo (Hi, Hf)
        df3 = pd.read_excel(xls, 'Sheet3')  # Parámetros globales
        df4 = pd.read_excel(xls, 'Sheet4')  # Configuración nodos y skills

        # Preprocesar columnas que contienen listas como strings "[1, 2]"
        for col in ['MSK1', 'MSK2', 'Skills']:
            df4[col] = df4[col].apply(lambda x: ast.literal_eval(str(x)) if isinstance(x, str) else x)
        
        return df1, df2, df3, df4
    except Exception as e:
        st.error(f"Error al leer el archivo: {e}")
        return None, None, None, None

def solve_chunk(chunk_df, df2, df3, df4, global_params, chunk_id, total_chunks):
    """
    Resuelve la optimización para un subconjunto de pedidos usando CP-SAT.
    """
    model = cp_model.CpModel()
    solver = cp_model.CpSolver()

    # Parámetros globales
    horizon = 10000  # Horizonte de tiempo suficiente (minutos u horas)
    
    # Estructuras de resultados
    starts = {}  # start_var
    ends = {}    # end_var
    presences = {} # bool_var (si se asigna a este muelle)
    
    # Agrupación para NoOverlap: intervals_per_dock[(nodo, muelle)] = [interval_var, ...]
    intervals_per_dock = {} 

    # --- 1. Variables y Restricciones por Pedido ---
    for idx, row in chunk_df.iterrows():
        p = row['p']
        origin_node = row['Origenes']
        dest_node = row['Destinos']
        skill_req = row['SKILL'] # 1 o 2
        
        # Tiempos de operación
        load_duration = int(row['TC'])
        unload_duration = int(row['TD'])
        travel_time = int(row['T'])
        
        # --- Nodo Origen ---
        # Obtener muelles válidos según Skill
        node_config_o = df4[df4['n'] == origin_node].iloc[0]
        valid_docks_o = node_config_o['MSK1'] if skill_req == 1 else node_config_o['MSK2']
        
        origin_intervals = []
        origin_presences = []
        
        # Variables maestras para el pedido (Inicio y Fin real)
        start_o_global = model.NewIntVar(0, horizon, f'start_o_glob_{p}')
        end_o_global = model.NewIntVar(0, horizon, f'end_o_glob_{p}')
        
        # Vincular duración global
        model.Add(end_o_global == start_o_global + load_duration)

        for m in valid_docks_o:
            # Ventanas de tiempo del muelle (Hard Constraints base, Soft constraints modelables)
            # Buscamos en df2 las ventanas para n y MS
            dock_window = df2[(df2['n'] == origin_node) & (df2['MS'] == m)]
            if not dock_window.empty:
                Hi = int(dock_window.iloc[0]['Hi'])
                Hf = int(dock_window.iloc[0]['Hf'])
            else:
                Hi, Hf = 0, horizon

            # Variable Booleana: ¿Se usa este muelle 'm' para el pedido 'p'?
            is_present = model.NewBoolVar(f'pres_o_{p}_{origin_node}_{m}')
            origin_presences.append(is_present)
            
            # Variables de intervalo opcional
            start_var = model.NewIntVar(Hi, horizon, f'start_o_{p}_{m}') # Debe respetar Hi
            end_var = model.NewIntVar(0, horizon + 24, f'end_o_{p}_{m}') # Hf flexible (soft)
            
            # Intervalo opcional: Solo activo si is_present es True
            interval_var = model.NewOptionalIntervalVar(
                start_var, load_duration, end_var, is_present, f'interval_o_{p}_{m}'
            )
            
            # Guardar para AddNoOverlap
            if (origin_node, m) not in intervals_per_dock:
                intervals_per_dock[(origin_node, m)] = []
            intervals_per_dock[(origin_node, m)].append(interval_var)
            
            # Vincular variable local con global: Si se elige este muelle, start_global == start_var
            model.Add(start_o_global == start_var).OnlyEnforceIf(is_present)
            
            # Restricción de ventana superior (Soft: Penalización si se pasa de Hf)
            # lateness = max(0, end_var - Hf)
            lateness = model.NewIntVar(0, horizon, f'late_o_{p}_{m}')
            model.Add(lateness >= end_var - Hf).OnlyEnforceIf(is_present)
            model.Add(lateness == 0).OnlyEnforceIf(is_present.Not()) # Si no se usa, lateness es 0
            
            # Guardar referencias
            presences[(p, 'origin', m)] = is_present
            starts[(p, 'origin', m)] = start_var
            ends[(p, 'origin', m)] = end_var

        # Restricción: Debe asignarse exactamente a 1 muelle en el origen
        model.Add(sum(origin_presences) == 1)

        # --- Nodo Destino ---
        node_config_d = df4[df4['n'] == dest_node].iloc[0]
        valid_docks_d = node_config_d['MSK1'] if skill_req == 1 else node_config_d['MSK2']
        
        dest_intervals = []
        dest_presences = []
        
        start_d_global = model.NewIntVar(0, horizon, f'start_d_glob_{p}')
        end_d_global = model.NewIntVar(0, horizon, f'end_d_glob_{p}')
        model.Add(end_d_global == start_d_global + unload_duration)

        for m in valid_docks_d:
            dock_window = df2[(df2['n'] == dest_node) & (df2['MS'] == m)]
            if not dock_window.empty:
                Hi = int(dock_window.iloc[0]['Hi'])
                Hf = int(dock_window.iloc[0]['Hf'])
            else:
                Hi, Hf = 0, horizon

            is_present = model.NewBoolVar(f'pres_d_{p}_{dest_node}_{m}')
            dest_presences.append(is_present)
            
            start_var = model.NewIntVar(Hi, horizon, f'start_d_{p}_{m}')
            end_var = model.NewIntVar(0, horizon + 24, f'end_d_{p}_{m}')
            
            interval_var = model.NewOptionalIntervalVar(
                start_var, unload_duration, end_var, is_present, f'interval_d_{p}_{m}'
            )
            
            if (dest_node, m) not in intervals_per_dock:
                intervals_per_dock[(dest_node, m)] = []
            intervals_per_dock[(dest_node, m)].append(interval_var)
            
            model.Add(start_d_global == start_var).OnlyEnforceIf(is_present)
            
            presences[(p, 'dest', m)] = is_present
            starts[(p, 'dest', m)] = start_var
            ends[(p, 'dest', m)] = end_var

        model.Add(sum(dest_presences) == 1)

        # --- Restricción de Viaje (Precedencia) ---
        # El inicio en destino >= Fin en origen + Tiempo de viaje
        model.Add(start_d_global >= end_o_global + travel_time)

    # --- 2. Restricciones de No Solapamiento (Disjunctive) ---
    for (n, m), intervals in intervals_per_dock.items():
        model.AddNoOverlap(intervals)

    # --- 3. Función Objetivo ---
    # Minimizar Makespan (tiempo total) + Prioridad
    # Gurobi obj era complejo. Aquí simplificamos para velocidad y robustez:
    # Min(Sum(StartTimes * Priority) + Penalties)
    
    obj_terms = []
    for idx, row in chunk_df.iterrows():
        p = row['p']
        prio = int(row['PR'])
        origin_node = row['Origenes']
        dest_node = row['Destinos']
        skill_req = row['SKILL']
        
        # Sumar tiempos de inicio ponderados por prioridad (menor prioridad = más rápido si PR es alto? Asumimos PR como peso de urgencia)
        # Nota: En Gurobi (PR * y) / ... -> Minimizar PR*Start.
        
        # Valid docks origin
        valid_docks_o = df4[df4['n'] == origin_node].iloc[0]['MSK1' if skill_req==1 else 'MSK2']
        for m in valid_docks_o:
            if (p, 'origin', m) in starts:
                # Costo por empezar tarde
                obj_terms.append(starts[(p, 'origin', m)] * prio)
                # Costo por usar muelles "extra" o salir de ventana (si hubiéramos añadido penalización explícita)
        
        # Valid docks dest
        valid_docks_d = df4[df4['n'] == dest_node].iloc[0]['MSK1' if skill_req==1 else 'MSK2']
        for m in valid_docks_d:
            if (p, 'dest', m) in starts:
                obj_terms.append(starts[(p, 'dest', m)] * prio)

    model.Minimize(sum(obj_terms))

    # --- 4. Resolver ---
    # Configuración solver para velocidad
    solver.parameters.max_time_in_seconds = 30.0  # Limite por lote
    solver.parameters.log_search_progress = False
    
    status = solver.Solve(model)

    results = []
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        for idx, row in chunk_df.iterrows():
            p = row['p']
            skill_req = row['SKILL']
            
            # Origen
            node_o = row['Origenes']
            docks_o = df4[df4['n'] == node_o].iloc[0]['MSK1' if skill_req==1 else 'MSK2']
            for m in docks_o:
                if solver.Value(presences[(p, 'origin', m)]) == 1:
                    results.append({
                        'Orden': p,
                        'Tipo': 'Origen',
                        'Nodo': node_o,
                        'Muelle': m,
                        'Inicio Servicio': solver.Value(starts[(p, 'origin', m)]),
                        'Fin Servicio': solver.Value(ends[(p, 'origin', m)]),
                        'Batch': f"{chunk_id}/{total_chunks}"
                    })
            
            # Destino
            node_d = row['Destinos']
            docks_d = df4[df4['n'] == node_d].iloc[0]['MSK1' if skill_req==1 else 'MSK2']
            for m in docks_d:
                if solver.Value(presences[(p, 'dest', m)]) == 1:
                    results.append({
                        'Orden': p,
                        'Tipo': 'Destino',
                        'Nodo': node_d,
                        'Muelle': m,
                        'Inicio Servicio': solver.Value(starts[(p, 'dest', m)]),
                        'Fin Servicio': solver.Value(ends[(p, 'dest', m)]),
                        'Batch': f"{chunk_id}/{total_chunks}"
                    })
    else:
        st.warning(f"No se encontró solución factible para el lote {chunk_id}")
        
    return results

def main():
    st.title("🚛 Sistema de Asignación de Muelles (SaaS)")
    st.markdown("""
    Esta aplicación optimiza la asignación de muelles y horarios para camiones eliminando la dependencia de Gurobi.
    Usa **Google OR-Tools** y divide problemas grandes en lotes automáticamente.
    """)

    uploaded_file = st.file_uploader("Cargar archivo de datos (Excel)", type=['xlsx'])

    if uploaded_file is not None:
        with st.spinner('Procesando archivo...'):
            df1, df2, df3, df4 = load_data(uploaded_file)

        if df1 is not None:
            st.success("Datos cargados correctamente.")
            st.write(f"Total de pedidos encontrados: **{len(df1)}**")
            
            with st.expander("Ver datos cargados"):
                st.dataframe(df1.head())

            if st.button("🚀 Iniciar Optimización"):
                start_time = time.time()
                
                # --- Lógica de Chunking ---
                total_orders = len(df1)
                chunk_size = 150 # Definido en requerimientos
                
                # Aleatorizar para evitar sesgos en el orden de entrada si se desea
                df1_shuffled = df1.sample(frac=1, random_state=42).reset_index(drop=True)
                
                chunks = np.array_split(df1_shuffled, np.ceil(total_orders / chunk_size))
                
                all_results = []
                progress_bar = st.progress(0)
                
                st.info(f"Procesando en {len(chunks)} lote(s) secuenciales...")
                
                for i, chunk in enumerate(chunks):
                    # Resolver lote
                    batch_res = solve_chunk(chunk, df2, df3, df4, df3.iloc[0], i+1, len(chunks))
                    all_results.extend(batch_res)
                    
                    # Actualizar barra de progreso
                    progress_bar.progress((i + 1) / len(chunks))
                
                end_time = time.time()
                
                if all_results:
                    st.success(f"Optimización completada en {round(end_time - start_time, 2)} segundos.")
                    
                    res_df = pd.DataFrame(all_results)
                    res_df = res_df.sort_values(by=['Orden', 'Inicio Servicio'])
                    
                    # Mostrar resultados
                    st.subheader("Resultados de Asignación")
                    st.dataframe(res_df)
                    
                    # Exportar a Excel
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        res_df.to_excel(writer, index=False, sheet_name='Resultados')
                    
                    st.download_button(
                        label="💾 Descargar Resultados (Excel)",
                        data=output.getvalue(),
                        file_name="planificacion_optimizada.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    
                    # Métricas clave
                    col1, col2 = st.columns(2)
                    col1.metric("Total Operaciones Programadas", len(res_df))
                    col2.metric("Hora Final del Plan (Min)", res_df['Fin Servicio'].max())
                    
                else:
                    st.error("No se pudieron generar resultados. Verifique la coherencia de los datos (Ventanas de tiempo muy estrictas).")

if __name__ == '__main__':
    main()
