import streamlit as st
import pandas as pd
import altair as alt
from ortools.sat.python import cp_model
import io
from datetime import datetime, timedelta

# --- Configuración de la Página ---
st.set_page_config(page_title="SaaS Logístico T1", layout="wide")

st.title("🚛 Optimizador de Muelles Inteligente (Modelo T1)")
st.markdown("""
**Diagnóstico en Tiempo Real:** Carga tus pedidos y obtén un itinerario ejecutable.
""")

# --- Funciones Auxiliares ---

def format_time(hours_float):
    """Convierte un número flotante (ej. 9.5) a formato hora (09:30)"""
    # Asumimos que el día empieza a las 00:00. Ajustar base si es necesario.
    base_time = datetime(2024, 1, 1, 0, 0, 0)
    delta = timedelta(hours=hours_float)
    target_time = base_time + delta
    # Si pasa de 24h, mostrar Día + Hora
    if hours_float >= 24:
        days = int(hours_float // 24)
        return f"+{days}d {target_time.strftime('%H:%M')}"
    return target_time.strftime('%H:%M')

def solve_logistics(df):
    # ... (AQUÍ VA TU LÓGICA DE OR-TOOLS QUE YA TIENES EN TU APP.PY ACTUAL) ...
    # ... (Copia y pega la función solve_optimization o la lógica que generó la IA antes) ...
    # ... (Asegúrate de que devuelva un DataFrame llamado 'results_df') ...
    
    # NOTA PARA EL USUARIO: Como no tengo tu código exacto de or-tools aquí, 
    # estoy simulando que esta función devuelve el dataframe que me mostraste.
    # Tú debes mantener tu lógica de optimización intacta aquí.
    pass 

# --- Interfaz de Usuario ---

uploaded_file = st.file_uploader("Cargar archivo de pedidos (Excel)", type=["xlsx"])

if uploaded_file:
    st.success("Archivo cargado. Procesando...")
    
    # 1. Leemos el archivo (Simulación de tu lógica actual)
    # df_input = pd.read_excel(uploaded_file)
    
    # 2. AQUÍ EJECUTAS TU MOTOR (Reemplaza esto con tu llamada real)
    # results_df = tu_funcion_de_optimizacion(df_input)
    
    # --- MODO DEMO: Usaré el CSV que subiste para mostrarte CÓMO visualizarlo ---
    # (En producción, borra este bloque y usa la salida real de tu motor)
    results_df = pd.read_csv("2026-01-12T18-23_export.csv") # OJO: Esto es solo para que veas el efecto ahora
    
    # --- 3. POST-PROCESAMIENTO (La Magia de la UX) ---
    
    # Convertir horas numéricas a legibles
    results_df['Hora Entrada'] = results_df['Inicio Servicio'].apply(format_time)
    results_df['Hora Salida'] = results_df['Fin Servicio'].apply(format_time)
    
    # Crear etiquetas legibles
    results_df['Etiqueta Muelle'] = "Muelle " + results_df['Muelle'].astype(str)
    results_df['Etiqueta Nodo'] = "Nodo " + results_df['Nodo'].astype(str)

    # --- 4. DASHBOARD DE RESULTADOS ---
    
    # KPIs
    col1, col2, col3 = st.columns(3)
    total_pedidos = results_df['Orden'].nunique()
    muelles_usados = results_df['Muelle'].nunique()
    tiempo_ciclo = results_df['Fin Servicio'].max()
    
    col1.metric("📦 Pedidos Programados", total_pedidos)
    col2.metric("🏭 Muelles Activos", muelles_usados)
    col3.metric("⏱️ Horizonte de Planificación", f"{tiempo_ciclo:.1f} Horas")
    
    st.divider()
    
    tab1, tab2, tab3 = st.tabs(["📊 Gantt Visual", "📋 Itinerario Detallado", "📥 Descargas"])
    
    with tab1:
        st.subheader("Visualización de Ocupación de Muelles")
        # Gráfica de Gantt con Altair
        chart = alt.Chart(results_df).mark_bar().encode(
            x=alt.X('Inicio Servicio', title='Hora Inicio (Numérica)'),
            x2='Fin Servicio',
            y=alt.Y('Etiqueta Muelle', title='Muelle', sort='ascending'),
            color=alt.Color('Etiqueta Nodo', legend=alt.Legend(title="Ubicación")),
            tooltip=['Orden', 'Hora Entrada', 'Hora Salida', 'Tipo']
        ).properties(width=800, height=400)
        
        st.altair_chart(chart, use_container_width=True)
        st.caption("Pasa el mouse sobre las barras para ver detalles del pedido.")

    with tab2:
        st.subheader("Agenda Operativa")
        # Mostrar tabla limpia
        display_cols = ['Orden', 'Tipo', 'Etiqueta Nodo', 'Etiqueta Muelle', 'Hora Entrada', 'Hora Salida']
        st.dataframe(results_df[display_cols], use_container_width=True)

    with tab3:
        st.subheader("Exportar Datos")
        
        # Generar Excel limpio
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            results_df[display_cols].to_excel(writer, index=False, sheet_name='Itinerario')
            results_df.to_excel(writer, index=False, sheet_name='Data Cruda') # Por si acaso
            
        st.download_button(
            label="Descargar Itinerario Oficial (.xlsx)",
            data=output.getvalue(),
            file_name="Plan_Logistico_Optimizado.xlsx",
            mime="application/vnd.ms-excel"
        )
