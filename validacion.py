import io
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

# =====================================================
# AUTOLANZADOR PARA VS CODE (Permite usar el botón ▶ Play)
# =====================================================

import pandas as pd
import psycopg2
from psycopg2 import errors
import pyodbc
import streamlit as st

# =====================================================
# CONFIGURACIÓN DE LA PÁGINA WEB
# =====================================================
st.set_page_config(
    page_title="Validador SQL Interactivo",
    page_icon="⚡",
    layout="wide",
)

st.markdown(
    """
    <style>
    .stMetric {background-color: #f0f2f6; padding: 12px; border-radius: 10px;}
    </style>
""",
    unsafe_allow_html=True,
)

# =====================================================
# CONFIGURACIÓN DE CREDENCIALES DE BASE DE DATOS
# =====================================================
load_dotenv()
DATABASE_FERRO = os.getenv("DATABASE_FERRO")
DATABASE_FOMAG = os.getenv("DATABASE_FOMAG")
DATABASE_MEDICINA_INTEGRAL = os.getenv("DATABASE_MEDICINA_INTEGRAL")
DATABASE_SUMIMEDICAL = os.getenv("DATABASE_SUMIMEDICAL")
DATABASE_DINAMICA = os.getenv("DATABASE_DINAMICA")


def construir_config(valor: str, tipo: str):
    """
    Construye el diccionario de configuración de conexión a partir de la
    variable de entorno correspondiente.

    - Para SQL Server, la variable ya trae la cadena ODBC completa
      (DRIVER=...;SERVER=...;DATABASE=...;UID=...;PWD=...), así que se
      guarda y se usa tal cual.
    - Para PostgreSQL, la variable es una URL
      (postgresql://usuario:clave@host:puerto/basedatos) y se parsea.
    """
    if not valor:
        return None

    if tipo == "sqlserver":
        return {"tipo": "sqlserver", "cadena": valor}

    elif tipo == "postgres":
        parsed = urlparse(valor)
        if not parsed.hostname:
            raise ValueError(
                f"No se pudo interpretar la URL de conexión PostgreSQL. "
                "Verifica el formato en el archivo .env "
                "(postgresql://usuario:clave@host:puerto/basedatos)."
            )
        return {
            "tipo": "postgres",
            "servidor": parsed.hostname,
            "puerto": parsed.port,
            "base": parsed.path.lstrip("/"),
            "usuario": parsed.username,
            "clave": parsed.password,
        }

    raise ValueError(f"Tipo de base de datos desconocido: '{tipo}'")


# Ferro, Fomag y Dinámica -> SQL Server | Medicina Integral y Sumimedical -> PostgreSQL
CONFIG_BD = {
    "Ferro": construir_config(DATABASE_FERRO, "sqlserver"),
    "Fomag": construir_config(DATABASE_FOMAG, "sqlserver"),
    "Dinámica": construir_config(DATABASE_DINAMICA, "sqlserver"),
    "Medicina Integral": construir_config(DATABASE_MEDICINA_INTEGRAL, "postgres"),
    "Sumimedical": construir_config(DATABASE_SUMIMEDICAL, "postgres"),
}

# Descarta cualquier base de datos cuya variable de entorno no esté definida,
# para que no aparezca en el selector si falta configurarla.
CONFIG_BD = {nombre: cfg for nombre, cfg in CONFIG_BD.items() if cfg is not None}

if not CONFIG_BD:
    st.error(
        "⚠️ No se encontró ninguna variable de entorno de base de datos configurada "
        "(DATABASE_FERRO, DATABASE_FOMAG, DATABASE_MEDICINA_INTEGRAL, "
        "DATABASE_SUMIMEDICAL, DATABASE_DINAMICA)."
    )
    st.stop()


# =====================================================
# FUNCIONES AUXILIARES
# =====================================================
def limpiar_query_sql(query: str) -> str:
    """Limpia saltos de línea de PowerQuery, espacios invisibles y comillas extremas."""
    if not query:
        return ""
    # Quita comillas dobles o simples iniciales/finales automáticamente
    query_limpio = query.strip().strip('"').strip("'")
    return (
        query_limpio.replace("#(lf)", "\n")
        .replace("\xa0", " ")
        .replace("\t", " ")
        .strip()
    )


def extraer_tabla_principal(query: str) -> str:
    """Extrae la tabla principal después del primer FROM."""
    query_limpia = limpiar_query_sql(query)
    if not query_limpia:
        return ""

    query_limpia = re.sub(r"(?i)\bFROM\b", " FROM ", query_limpia)
    patron = r"(?i)\bFROM\s+([a-zA-Z0-9_\.]+)"
    coincidencia = re.search(patron, query_limpia)
    return coincidencia.group(1) if coincidencia else ""


def obtener_conexion(config):
    """Crea la conexión a la base de datos correspondiente."""
    tipo = config["tipo"]
    if tipo == "sqlserver":
        return pyodbc.connect(config["cadena"])
    elif tipo == "postgres":
        return psycopg2.connect(
            database=config["base"],
            user=config["usuario"],
            host=config["servidor"],
            password=config["clave"],
            port=config["puerto"],
        )


def ejecutar_conteo(conexion, query_sql: str) -> int:
    cursor = conexion.cursor()
    cursor.execute(query_sql)
    resultado = cursor.fetchone()[0]
    cursor.close()
    return resultado


def obtener_vista_previa(conexion, query_sql: str, tipo_bd: str):
    """Retorna un DataFrame con una muestra de 10 filas."""
    query_limpio = limpiar_query_sql(query_sql)
    if tipo_bd == "sqlserver":
        sql_muestra = f"SELECT TOP 50 * FROM ({query_limpio}) AS sample_q"
    else:
        sql_muestra = f"SELECT * FROM ({query_limpio}) AS sample_q LIMIT 100"

    return pd.read_sql(sql_muestra, conexion)


# =====================================================
# INTERFAZ DE USUARIO (BARRA LATERAL)
# =====================================================
with st.sidebar:
    LOGO_SUMI = "https://sumimedical.com/wp-content/uploads/2023/11/logo-sumimedical.png"
    st.image(LOGO_SUMI, width="stretch")
    st.markdown("---")

    st.header("⚙️ Selecciona la Base de datos")
    opcion_bd = st.selectbox(
        "Servidor Origen:",
        options=list(CONFIG_BD.keys()),
    )
    config_actual = CONFIG_BD[opcion_bd]


# =====================================================
# PANEL PRINCIPAL
# =====================================================
st.title("⚡ Validador de base de datos")
st.write(
    "Ingresa tu Query para detectar automáticamente la tabla principal y comparar los datos del query y la tabla principal."
)

col_izq, col_der = st.columns([2, 1])

with col_izq:
    query_usuario = st.text_area(
        "📝 Pega tu Query aquí (las comillas se eliminan automáticamente):",
        height=220,
        placeholder='SELECT * FROM mi_tabla WHERE ... o "SELECT * FROM mi_tabla"',
    )

tabla_auto = extraer_tabla_principal(query_usuario)

with col_der:
    st.subheader("🔍 Tabla Detectada")
    tabla_final = st.text_input(
        "Tabla principal (ajustable):",
        value=tabla_auto,
        help="Detectada automáticamente del primer 'FROM'. Puedes cambiarla si la consulta es muy compleja.",
    )

    ver_muestra = st.checkbox("Ver muestra de datos (50 filas)")

st.markdown("---")

# Botón Principal
if st.button("🚀 Comparar Registros", type="primary", width="stretch"):
    query_limpio = limpiar_query_sql(query_usuario)

    if not query_limpio:
        st.error("⚠️ La consulta SQL no puede estar vacía.")
    elif not tabla_final.strip():
        st.error(
            "⚠️ No se pudo determinar la tabla principal. Verifícala en la caja de texto."
        )
    else:
        with st.spinner(f"Procesando en **{opcion_bd}**..."):
            try:
                conn = obtener_conexion(config_actual)

                sql_tabla_principal = (
                    f"SELECT COUNT(*) FROM {tabla_final.strip()}"
                )
                sql_query_ajustado = (
                    f"SELECT COUNT(*) FROM ({query_limpio}) AS q"
                )

                rec_tabla = ejecutar_conteo(conn, sql_tabla_principal)
                rec_query = ejecutar_conteo(conn, sql_query_ajustado)

                st.markdown("### 📊 Resultado de la Comparación")
                m1, m2, m3 = st.columns(3)

                diferencia = rec_query - rec_tabla
                m1.metric("Tabla Base", f"{rec_tabla:,}")
                m2.metric("Tu Query", f"{rec_query:,}")
                m3.metric(
                    "Diferencia",
                    f"{diferencia:,}",
                    delta_color="inverse" if diferencia != 0 else "normal",
                )

                if rec_query == rec_tabla:
                    st.success(
                        "🟢 **Coincidencia Exacta:** El query devuelve la misma cantidad de registros que la tabla principal."
                    )
                elif rec_query < rec_tabla:
                    st.warning(
                        f"🟡 **Filtro Aplicado:** El query devuelve **{abs(diferencia):,} registros menos**. Existen condiciones WHERE o JOINs de filtro."
                    )
                else:
                    st.error(
                        f"🔴 **Posible Duplicación:** El query genera **{diferencia:,} registros adicionales**. Revisa si los JOINs están duplicando filas."
                    )

                if ver_muestra:
                    st.markdown("---")
                    st.markdown("### 👁️ Vista Previa (Primeros 10 registros)")
                    df_preview = obtener_vista_previa(
                        conn, query_limpio, config_actual["tipo"]
                    )
                    st.dataframe(df_preview, width="stretch")

                conn.close()

            except Exception as e:
                st.error("❌ Origen de datos incorrecto o la tabla no existe")
                with st.expander("Ver detalle técnico"):
                    st.code(str(e))

