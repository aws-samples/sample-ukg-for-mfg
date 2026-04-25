-- =============================================================================
-- Digital Thread POC - Simulated Manufacturing Data
-- =============================================================================
-- Creates schemas and tables simulating 3 plants with different ERP/MES/CMMS
-- systems. Each schema represents a different manufacturing system.
--
-- Run via: psql -h <rds-endpoint> -U admin -d postgres -f seed_manufacturing.sql
-- Or via:  python data/rds/seed_rds.py --region us-east-1
-- =============================================================================

-- =============================================
-- SAP S/4HANA - Indianapolis Plant (ERP)
-- =============================================
CREATE SCHEMA IF NOT EXISTS sap_indianapolis;

CREATE TABLE IF NOT EXISTS sap_indianapolis.aufk (
    aufnr   VARCHAR(12) PRIMARY KEY,
    auart   VARCHAR(4),
    matnr   VARCHAR(18),
    gamng   DECIMAL(13,3),
    gstrp   DATE,
    gltrp   DATE,
    status  VARCHAR(20)
);

INSERT INTO sap_indianapolis.aufk VALUES
('4521', 'PP01', '000000000000882', 500, '2026-02-10', '2026-02-18', 'DELAYED'),
('4522', 'PP01', '000000000000883', 200, '2026-02-12', '2026-02-20', 'RELEASED'),
('4523', 'PP01', '000000000000882', 300, '2026-02-14', '2026-02-22', 'RELEASED'),
('4524', 'PP01', '000000000000884', 150, '2026-02-16', '2026-02-25', 'RELEASED'),
('4525', 'PP01', '000000000000882', 400, '2026-02-18', '2026-02-28', 'CREATED')
ON CONFLICT (aufnr) DO NOTHING;

CREATE TABLE IF NOT EXISTS sap_indianapolis.equi (
    equnr        VARCHAR(18) PRIMARY KEY,
    eqktx        VARCHAR(80),
    swerk        VARCHAR(20),
    inbdt        DATE,
    manufacturer VARCHAR(40)
);

INSERT INTO sap_indianapolis.equi VALUES
('10004421', 'CMX-441 5-axis Milling Center Line 3', 'Indianapolis', '2019-06-15', 'DMG Mori'),
('10004422', 'CMX-442 5-axis Milling Center Line 3', 'Indianapolis', '2020-03-22', 'DMG Mori'),
('10004423', 'CTX-310 CNC Turning Center Line 2',    'Indianapolis', '2018-11-01', 'DMG Mori')
ON CONFLICT (equnr) DO NOTHING;

CREATE TABLE IF NOT EXISTS sap_indianapolis.mara (
    matnr VARCHAR(18) PRIMARY KEY,
    maktx VARCHAR(80),
    meins VARCHAR(4),
    mtart VARCHAR(4)
);

INSERT INTO sap_indianapolis.mara VALUES
('000000000000882', 'Precision Gear Assembly SKU-882', 'EA', 'FERT'),
('000000000000883', 'Drive Shaft Component SKU-883',   'EA', 'FERT'),
('000000000000884', 'Bearing Housing SKU-884',         'EA', 'HALB')
ON CONFLICT (matnr) DO NOTHING;

-- =============================================
-- Ignition MES - Indianapolis Plant
-- =============================================
CREATE SCHEMA IF NOT EXISTS mes_indianapolis;

CREATE TABLE IF NOT EXISTS mes_indianapolis.production_runs (
    run_id        SERIAL PRIMARY KEY,
    order_number  VARCHAR(12),
    line          VARCHAR(20),
    machine       VARCHAR(30),
    planned_qty   INTEGER,
    completed_qty INTEGER,
    scrap_qty     INTEGER,
    start_time    TIMESTAMP,
    end_time      TIMESTAMP,
    status        VARCHAR(20)
);

INSERT INTO mes_indianapolis.production_runs
    (order_number, line, machine, planned_qty, completed_qty, scrap_qty, start_time, end_time, status)
VALUES
('4521', 'Line 3', 'CMX-441', 500, 340, 12, '2026-02-10 06:00', '2026-02-16 14:30', 'STOPPED'),
('4522', 'Line 3', 'CMX-442', 200, 200,  3, '2026-02-12 06:00', '2026-02-15 18:00', 'COMPLETED'),
('4523', 'Line 3', 'CMX-442', 300, 180,  5, '2026-02-16 06:00', NULL,               'RUNNING')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS mes_indianapolis.machine_status (
    machine     VARCHAR(30) PRIMARY KEY,
    line        VARCHAR(20),
    status      VARCHAR(20),
    last_update TIMESTAMP,
    reason      TEXT
);

INSERT INTO mes_indianapolis.machine_status VALUES
('CMX-441', 'Line 3', 'DOWN',    '2026-02-16 14:30', 'Unplanned maintenance - bearing failure'),
('CMX-442', 'Line 3', 'RUNNING', '2026-02-18 08:00', NULL),
('CTX-310', 'Line 2', 'RUNNING', '2026-02-18 08:00', NULL)
ON CONFLICT (machine) DO UPDATE
    SET status = EXCLUDED.status, last_update = EXCLUDED.last_update, reason = EXCLUDED.reason;

CREATE TABLE IF NOT EXISTS mes_indianapolis.oee_daily (
    id           SERIAL PRIMARY KEY,
    line         VARCHAR(20),
    date         DATE,
    oee          DECIMAL(5,2),
    availability DECIMAL(5,2),
    performance  DECIMAL(5,2),
    quality      DECIMAL(5,2)
);

INSERT INTO mes_indianapolis.oee_daily (line, date, oee, availability, performance, quality) VALUES
('Line 3', '2026-02-13', 82.1, 90.0, 94.0, 97.0),
('Line 3', '2026-02-14', 78.5, 85.0, 95.0, 97.2),
('Line 3', '2026-02-15', 72.1, 80.0, 93.0, 96.9),
('Line 3', '2026-02-16', 34.2, 42.0, 88.0, 92.5),
('Line 3', '2026-02-17',  0.0,  0.0,  0.0,  0.0),
('Line 3', '2026-02-18', 65.3, 75.0, 90.0, 96.8)
ON CONFLICT DO NOTHING;

-- =============================================
-- IBM Maximo - Indianapolis Plant (CMMS)
-- =============================================
CREATE SCHEMA IF NOT EXISTS maximo_indianapolis;

CREATE TABLE IF NOT EXISTS maximo_indianapolis.asset (
    assetnum    VARCHAR(20) PRIMARY KEY,
    description TEXT,
    location    VARCHAR(30),
    status      VARCHAR(20),
    manufacturer VARCHAR(40),
    serialnum   VARCHAR(40)
);

INSERT INTO maximo_indianapolis.asset VALUES
('CMX-441-IND', 'CMX-441 5-axis Milling Center', 'Line 3', 'OPERATING', 'DMG Mori', 'CMX441-2019-0087'),
('CMX-442-IND', 'CMX-442 5-axis Milling Center', 'Line 3', 'OPERATING', 'DMG Mori', 'CMX442-2020-0112'),
('CTX-310-IND', 'CTX-310 CNC Turning Center',    'Line 2', 'OPERATING', 'DMG Mori', 'CTX310-2018-0045')
ON CONFLICT (assetnum) DO NOTHING;

CREATE TABLE IF NOT EXISTS maximo_indianapolis.workorder (
    wonum       VARCHAR(20) PRIMARY KEY,
    assetnum    VARCHAR(20),
    description TEXT,
    status      VARCHAR(20),
    reportdate  TIMESTAMP,
    schedstart  TIMESTAMP,
    actfinish   TIMESTAMP,
    worktype    VARCHAR(10),
    failurecode VARCHAR(20)
);

INSERT INTO maximo_indianapolis.workorder VALUES
('WO-2026-0891', 'CMX-441-IND', 'Unplanned - bearing failure spindle assembly', 'COMP', '2026-02-16 14:45', '2026-02-16 15:00', '2026-02-17 22:00', 'EM', 'BEARING'),
('WO-2026-0885', 'CMX-441-IND', 'Scheduled PM - spindle lubrication',           'COMP', '2026-02-01 08:00', '2026-02-03 06:00', '2026-02-03 10:00', 'PM', NULL),
('WO-2026-0878', 'CMX-442-IND', 'Scheduled PM - coolant system flush',          'COMP', '2026-01-28 08:00', '2026-01-30 06:00', '2026-01-30 09:00', 'PM', NULL)
ON CONFLICT (wonum) DO NOTHING;

-- =============================================
-- Oracle EBS - Pune Plant (ERP)
-- =============================================
CREATE SCHEMA IF NOT EXISTS oracle_pune;

CREATE TABLE IF NOT EXISTS oracle_pune.wip_discrete_jobs (
    wip_entity_name VARCHAR(240) PRIMARY KEY,
    primary_item_id INTEGER,
    start_quantity  DECIMAL(13,3),
    date_released   DATE,
    date_completed  DATE,
    status_type     INTEGER
);

INSERT INTO oracle_pune.wip_discrete_jobs VALUES
('WO-PUN-2026-0341', 44821, 400, '2026-01-15', '2026-01-28', 4),
('WO-PUN-2026-0342', 44821, 350, '2026-01-20', NULL,         12),
('WO-PUN-2026-0355', 44822, 600, '2026-02-01', NULL,          3),
('WO-PUN-2026-0360', 44821, 500, '2026-02-10', NULL,          3)
ON CONFLICT (wip_entity_name) DO NOTHING;

-- =============================================
-- Custom ERP - Monterrey Plant (Spanish)
-- =============================================
CREATE SCHEMA IF NOT EXISTS custom_monterrey;

CREATE TABLE IF NOT EXISTS custom_monterrey.ordenes_trabajo (
    numero_orden        VARCHAR(20) PRIMARY KEY,
    producto_id         VARCHAR(20),
    cantidad_planificada INTEGER,
    fecha_inicio        DATE,
    fecha_fin           DATE,
    estado              VARCHAR(20)
);

INSERT INTO custom_monterrey.ordenes_trabajo VALUES
('OT-MTY-2026-0101', 'mat_882', 250, '2026-02-05', '2026-02-15', 'COMPLETADO'),
('OT-MTY-2026-0102', 'mat_883', 150, '2026-02-10', NULL,         'RETRASADO'),
('OT-MTY-2026-0103', 'mat_882', 300, '2026-02-12', NULL,         'EN_PROCESO'),
('OT-MTY-2026-0104', 'mat_884', 100, '2026-02-15', NULL,         'PENDIENTE')
ON CONFLICT (numero_orden) DO NOTHING;
