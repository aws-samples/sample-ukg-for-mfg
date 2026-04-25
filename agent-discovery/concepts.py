"""Canonical manufacturing vocabulary for the Discovery Agent.

This module defines the comprehensive list of manufacturing Concepts used to
semantically map fields across registered systems. The Discovery Agent maps
every discovered field to one of these concepts during the Understanding phase.

Concepts are organized by ISA-95 domain and cover the four primary information
categories defined by ISA-95 Part 2 (Material, Equipment, Physical Asset,
Personnel) plus operational domains (Production, Maintenance, Quality,
Inventory) and modern manufacturing extensions (IoT/SCADA, Energy, Safety,
Product Lifecycle, Supply Chain).

Each concept carries:
  - id:          Canonical identifier (kebab-case)
  - domain:      ISA-95 domain grouping
  - description: Human-readable description
  - aliases:     Curated list of common field names seen in real systems

New concepts can be added without schema changes — no migration required.

Alias sources:
  - Curated aliases are defined here and always trusted.
  - Learned aliases are stored in DynamoDB (ALIAS# items in the registry
    table) and merged at runtime via ``get_all_concepts()``.

See: ISA-95 (IEC 62264), ISA-88 (IEC 61512), OPC UA for ISA-95 (OPC 30060),
     Purdue Reference Model (ISA-95 Part 1, Levels 0-4)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Concept:
    """A single canonical manufacturing concept.

    Attributes:
        id:          Kebab-case identifier (e.g. ``"work-order"``).
        domain:      ISA-95 domain this concept belongs to.
        description: Short human-readable description.
        aliases:     Common field names seen in real systems (curated).
    """

    id: str
    domain: str
    description: str
    aliases: list[str] = field(default_factory=list)

    @property
    def qualified_id(self) -> str:
        """Return domain-qualified identifier, e.g. ``"production.work-order"``."""
        return f"{self.domain}.{self.id}"


# ============================================================================
# ISA-95 Core Resource Models (Part 2)
# ============================================================================

# --- Equipment (role-based identification, Purdue Level 2-3) ---
# ISA-95 defines equipment by the role it plays, not its physical identity.
# Equipment hierarchy: Enterprise > Site > Area > Work Center > Work Unit
# See: OPC UA ISA-95 §7.4.1 ISA95EquipmentElementLevelEnum

_EQUIPMENT_CONCEPTS = [
    Concept("equipment-id", "equipment",
            "Tag/role identifier for equipment (e.g. TT-101)",
            ["tag", "equip_id", "equipment_tag", "equip_tag", "machine_id",
             "functional_location", "floc", "tag_number", "instrument_tag"]),
    Concept("equipment-class", "equipment",
            "Classification of equipment (e.g. reactor, CNC, conveyor)",
            ["equip_class", "equipment_type", "equip_type", "machine_type",
             "asset_category", "equipment_category"]),
    Concept("equipment-property", "equipment",
            "Named property of equipment (e.g. volume, speed, capacity)",
            ["equip_property", "equip_attr", "equipment_attribute",
             "machine_property", "equip_param"]),
    Concept("equipment-capability", "equipment",
            "What equipment can do — capacity, rate, throughput limits",
            ["equip_capability", "capacity", "rated_capacity",
             "max_throughput", "equip_rating"]),
    Concept("work-center", "equipment",
            "ISA-95 work center — production line, process cell, or mfg cell",
            ["workcenter", "work_center", "work_ctr", "process_cell",
             "manufacturing_cell", "production_area", "line_id"]),
    Concept("work-unit", "equipment",
            "Individual machine or station within a work center",
            ["workunit", "work_unit", "station", "station_id",
             "machine", "work_station"]),
    Concept("production-line", "equipment",
            "Sequence of work centers for a product flow (discrete mfg)",
            ["prod_line", "production_line", "line", "assembly_line",
             "mfg_line"]),
    Concept("storage-zone", "equipment",
            "Warehouse zone, tank farm, silo, bin location",
            ["storage_zone", "warehouse_zone", "tank_farm", "silo",
             "bin_location", "storage_area"]),
]

# --- Physical Asset (serial-number-based identification) ---
# ISA-95 distinguishes physical assets (tracked by serial number) from
# equipment (tracked by role/tag). A physical asset can be swapped into
# different equipment roles over its lifecycle.

_PHYSICAL_ASSET_CONCEPTS = [
    Concept("asset-id", "physical-asset",
            "Unique physical asset identifier (serial number, asset tag)",
            ["asset_id", "serial_number", "asset_tag", "asset_num",
             "physical_asset_id", "fixed_asset_id", "asset_no"]),
    Concept("asset-class", "physical-asset",
            "Make/model classification of physical asset",
            ["asset_class", "asset_type", "make_model", "model_number",
             "physical_asset_class", "equipment_model"]),
    Concept("asset-property", "physical-asset",
            "Named property of a physical asset (e.g. weight, dimensions)",
            ["asset_property", "asset_attr", "asset_attribute",
             "physical_asset_property"]),
    Concept("asset-location", "physical-asset",
            "Physical location of an asset (building, floor, bay)",
            ["asset_location", "location", "install_location",
             "physical_location", "asset_position"]),
    Concept("asset-status", "physical-asset",
            "Current status (in-service, spare, decommissioned)",
            ["asset_status", "status", "equip_status",
             "operational_status", "service_status"]),
    Concept("asset-lifecycle-state", "physical-asset",
            "Lifecycle phase (commissioned, operating, retired)",
            ["lifecycle_state", "lifecycle_phase", "asset_lifecycle",
             "asset_state", "commission_status"]),
    Concept("asset-hierarchy", "physical-asset",
            "Parent-child relationship between physical assets",
            ["parent_asset", "child_asset", "asset_parent",
             "superior_asset", "asset_hierarchy"]),
]

# --- Material (ISA-95 Part 2 Material Model) ---
# Material Class → Material Definition → Material Lot → Material Sublot
# See: OPC UA ISA-95 §4.2.4 Figure 3 — Material Model

_MATERIAL_CONCEPTS = [
    Concept("material", "material",
            "Generic material identifier",
            ["material_id", "mat_id", "material_code", "item_id",
             "item_number", "item_code", "stock_code"]),
    Concept("material-class", "material",
            "Classification of material (raw, WIP, finished goods)",
            ["material_class", "mat_class", "material_type", "mat_type",
             "item_type", "item_category", "stock_type"]),
    Concept("material-definition", "material",
            "Specific material type (e.g. Acetic Acid Grade 4)",
            ["material_definition", "mat_def", "material_spec",
             "item_definition", "product_spec"]),
    Concept("material-lot", "material",
            "Unique lot instance of a material definition",
            ["material_lot", "mat_lot", "lot_id", "lot_code",
             "batch_lot", "receiving_lot"]),
    Concept("material-sublot", "material",
            "Sub-division of a material lot (e.g. barrel, pallet, drum)",
            ["material_sublot", "sublot", "sublot_id", "container_id",
             "barrel_id", "pallet_id"]),
    Concept("material-property", "material",
            "Named property of material (pH, viscosity, grade, purity)",
            ["material_property", "mat_property", "mat_attr",
             "item_property", "material_attribute"]),
    Concept("material-test-spec", "material",
            "Test specification for material qualification",
            ["material_test_spec", "mat_test", "incoming_test",
             "material_qualification", "mat_qual_test"]),
]

# --- Personnel (ISA-95 Part 2 Personnel Model) ---
# Personnel Class → Person, with qualifications and test specs.
# See: OPC UA ISA-95 §4.2.4 Figure 4 — Personnel Model

_PERSONNEL_CONCEPTS = [
    Concept("operator", "personnel",
            "Person performing production operations",
            ["operator", "operator_id", "operator_name", "op_id",
             "worker", "worker_id", "technician"]),
    Concept("personnel-class", "personnel",
            "Role classification (e.g. Technician, Inspector, Draftsman)",
            ["personnel_class", "role", "job_role", "job_title",
             "position", "craft", "trade"]),
    Concept("personnel-qualification", "personnel",
            "Training/certification held by a person",
            ["qualification", "certification", "training",
             "license", "competency", "skill"]),
    Concept("personnel-id", "personnel",
            "Unique person identifier (badge, employee ID)",
            ["personnel_id", "person_id", "employee_id", "badge",
             "badge_id", "emp_id", "emp_no", "staff_id"]),
]


# ============================================================================
# Production Operations (ISA-95 Part 3 — Production, Purdue Level 3)
# ============================================================================
# ISA-95 Part 3 defines operations management: production, maintenance,
# quality, and inventory. Production operations cover scheduling, execution,
# and performance reporting.

_PRODUCTION_CONCEPTS = [
    Concept("work-order", "production",
            "Production order / manufacturing order",
            ["work_order", "wo", "mfg_order", "prod_order", "shop_order",
             "job_number", "job_id", "production_order", "order_number"]),
    Concept("production-run", "production",
            "Discrete execution of a work order on equipment",
            ["production_run", "run_id", "run_number", "batch_run",
             "mfg_run", "execution_id"]),
    Concept("production-schedule", "production",
            "Planned sequence of work orders",
            ["production_schedule", "schedule", "prod_schedule",
             "master_schedule", "mps"]),
    Concept("production-request", "production",
            "Request from ERP to produce (Level 4 → Level 3)",
            ["production_request", "prod_request", "mfg_request"]),
    Concept("production-response", "production",
            "Actual production results reported back (Level 3 → Level 4)",
            ["production_response", "prod_response", "production_report",
             "mfg_report"]),
    Concept("routing", "production",
            "Sequence of operations for a product",
            ["routing", "route", "routing_id", "process_route",
             "operation_sequence"]),
    Concept("routing-step", "production",
            "Individual operation within a routing",
            ["routing_step", "operation", "op_number", "op_id",
             "step", "process_step", "operation_number"]),
    Concept("process-segment", "production",
            "ISA-95 process segment — reusable process definition",
            ["process_segment", "segment", "segment_id",
             "process_definition"]),
    Concept("batch-id", "production",
            "Batch identifier for batch/process manufacturing",
            ["batch_id", "batch", "batch_number", "batch_no",
             "batch_code"]),
    Concept("lot-number", "production",
            "Lot number for traceability",
            ["lot_number", "lot_no", "lot", "lot_id", "lot_code"]),
    Concept("serial-number", "production",
            "Individual unit serial number (discrete manufacturing)",
            ["serial_number", "serial_no", "sn", "serial",
             "unit_serial"]),
    Concept("recipe", "production",
            "ISA-88 recipe (master, control, or site recipe)",
            ["recipe", "recipe_id", "recipe_name", "formula",
             "formula_id", "master_recipe"]),
    Concept("recipe-parameter", "production",
            "Parameter within a recipe (setpoint, duration, temperature)",
            ["recipe_parameter", "recipe_param", "formula_param",
             "recipe_setpoint"]),
    Concept("bom", "production",
            "Bill of Materials",
            ["bom", "bom_id", "bill_of_materials", "bom_number"]),
    Concept("bom-item", "production",
            "Individual line item in a BOM",
            ["bom_item", "bom_line", "bom_component", "component",
             "bom_detail"]),
    Concept("bom-level", "production",
            "BOM hierarchy level (single-level vs multi-level)",
            ["bom_level", "bom_depth", "bom_tier"]),
    Concept("cycle-time", "production",
            "Time to complete one unit/cycle",
            ["cycle_time", "ct", "cycle_duration", "takt"]),
    Concept("takt-time", "production",
            "Required pace to meet demand",
            ["takt_time", "takt", "demand_rate"]),
    Concept("lead-time", "production",
            "Total time from order to delivery",
            ["lead_time", "lt", "delivery_lead_time", "mfg_lead_time"]),
    Concept("throughput", "production",
            "Production rate (units per time period)",
            ["throughput", "output_rate", "production_rate",
             "units_per_hour", "uph"]),
    Concept("yield", "production",
            "Percentage of good output vs total input",
            ["yield", "yield_pct", "yield_rate", "good_yield"]),
    Concept("first-pass-yield", "production",
            "Percentage passing quality on first attempt",
            ["first_pass_yield", "fpy", "fpy_rate", "first_time_yield"]),
    Concept("changeover-time", "production",
            "Time to switch between products on a line",
            ["changeover_time", "changeover", "co_time",
             "product_changeover"]),
    Concept("setup-time", "production",
            "Time to prepare equipment for a run",
            ["setup_time", "setup", "prep_time", "setup_duration"]),
    Concept("shift-schedule", "production",
            "Shift definition (start, end, crew assignment)",
            ["shift_schedule", "shift_def", "shift_pattern"]),
    Concept("shift-id", "production",
            "Identifier for a specific shift instance",
            ["shift_id", "shift", "shift_number", "shift_code"]),
    Concept("crew-id", "production",
            "Identifier for a production crew/team",
            ["crew_id", "crew", "team_id", "gang_id", "crew_code"]),
]

# ============================================================================
# Maintenance Operations (ISA-95 Part 3 — Maintenance, Purdue Level 3)
# ============================================================================
# Maintenance operations cover PM, CM, PdM, and asset health management.
# Typically managed by CMMS/EAM systems (e.g. Maximo, SAP PM).

_MAINTENANCE_CONCEPTS = [
    Concept("maintenance-event", "maintenance",
            "Any maintenance activity (PM, CM, PdM)",
            ["maintenance_event", "maint_event", "maint_activity",
             "work_activity"]),
    Concept("work-order", "maintenance",
            "Formal maintenance work order",
            ["work_order", "wo", "maint_order", "pm_order",
             "service_order", "wo_number", "work_order_num",
             "notification"]),
    Concept("maintenance-request", "maintenance",
            "Request for maintenance (from operator or system)",
            ["maintenance_request", "maint_request", "service_request",
             "work_request", "notification", "maint_notification"]),
    Concept("maintenance-type", "maintenance",
            "PM, CM, PdM, emergency, shutdown",
            ["maintenance_type", "maint_type", "wo_type",
             "order_type", "work_type"]),
    Concept("maintenance-priority", "maintenance",
            "Priority/urgency of maintenance task",
            ["maintenance_priority", "priority", "urgency",
             "wo_priority", "maint_priority"]),
    Concept("failure-code", "maintenance",
            "Standardized failure/fault code (ISA-14224 taxonomy)",
            ["failure_code", "fault_code", "fail_code",
             "problem_code", "cause_code"]),
    Concept("failure-mode", "maintenance",
            "How equipment failed (FMEA failure mode)",
            ["failure_mode", "fail_mode", "fmea_mode",
             "failure_mechanism"]),
    Concept("root-cause", "maintenance",
            "Root cause of a failure or defect",
            ["root_cause", "cause", "rca", "root_cause_code",
             "cause_description"]),
    Concept("corrective-action", "maintenance",
            "Action taken to fix a problem",
            ["corrective_action", "fix", "remedy", "repair_action",
             "resolution"]),
    Concept("preventive-action", "maintenance",
            "Action taken to prevent future problems",
            ["preventive_action", "prevention", "preventive_measure"]),
    Concept("spare-part", "maintenance",
            "Replacement part used in maintenance",
            ["spare_part", "part", "part_number", "spare",
             "replacement_part", "component_part"]),
    Concept("spare-part-consumption", "maintenance",
            "Record of spare part usage in a maintenance event",
            ["spare_part_consumption", "part_usage", "parts_used",
             "material_usage"]),
    Concept("downtime-event", "maintenance",
            "Period of equipment unavailability",
            ["downtime_event", "downtime", "outage",
             "equipment_downtime", "unplanned_downtime"]),
    Concept("downtime-reason", "maintenance",
            "Categorized reason for downtime",
            ["downtime_reason", "reason_code", "downtime_code",
             "outage_reason"]),
    Concept("downtime-duration", "maintenance",
            "Length of a downtime event",
            ["downtime_duration", "duration", "outage_duration",
             "downtime_hours"]),
    Concept("mtbf", "maintenance",
            "Mean Time Between Failures",
            ["mtbf", "mean_time_between_failures"]),
    Concept("mttr", "maintenance",
            "Mean Time To Repair",
            ["mttr", "mean_time_to_repair"]),
    Concept("mttf", "maintenance",
            "Mean Time To Failure",
            ["mttf", "mean_time_to_failure"]),
]


# ============================================================================
# Quality Operations (ISA-95 Part 3 — Quality, Purdue Level 3)
# ============================================================================
# Quality operations cover testing, inspection, SPC, and nonconformance.
# Typically managed by LIMS or QMS systems.

_QUALITY_CONCEPTS = [
    Concept("quality-event", "quality",
            "Any quality-related event (test, inspection, NCR)",
            ["quality_event", "qa_event", "qc_event"]),
    Concept("quality-test", "quality",
            "Specific quality test execution",
            ["quality_test", "qa_test", "qc_test", "test_id",
             "lab_test", "test_execution"]),
    Concept("quality-test-spec", "quality",
            "Test specification / method definition",
            ["quality_test_spec", "test_spec", "test_method",
             "test_procedure", "qa_spec"]),
    Concept("quality-result", "quality",
            "Result of a quality test (pass/fail + measured values)",
            ["quality_result", "test_result", "qa_result",
             "qc_result", "inspection_result"]),
    Concept("inspection-record", "quality",
            "Record of an inspection activity",
            ["inspection_record", "inspection", "inspection_id",
             "insp_record", "visual_inspection"]),
    Concept("nonconformance", "quality",
            "Nonconformance report (NCR)",
            ["nonconformance", "ncr", "nc_report", "nonconformity",
             "deviation", "discrepancy"]),
    Concept("capa", "quality",
            "Corrective and Preventive Action record",
            ["capa", "capa_id", "corrective_preventive_action"]),
    Concept("defect-type", "quality",
            "Classification of defect",
            ["defect_type", "defect_code", "defect_class",
             "reject_reason", "defect_category"]),
    Concept("defect-count", "quality",
            "Number of defects found",
            ["defect_count", "defects", "reject_count",
             "defect_qty", "nc_count"]),
    Concept("scrap-rate", "quality",
            "Percentage of scrapped output",
            ["scrap_rate", "scrap_pct", "scrap_percent",
             "waste_rate"]),
    Concept("rework-event", "quality",
            "Record of rework activity",
            ["rework_event", "rework", "rework_id",
             "rework_order"]),
    Concept("spc-measurement", "quality",
            "Statistical Process Control measurement",
            ["spc_measurement", "spc", "spc_value",
             "control_chart_value"]),
    Concept("control-limit", "quality",
            "Upper/lower control limits for SPC",
            ["control_limit", "ucl", "lcl", "upper_control_limit",
             "lower_control_limit"]),
    Concept("cpk", "quality",
            "Process capability index",
            ["cpk", "cp", "process_capability", "capability_index"]),
    Concept("dpmo", "quality",
            "Defects Per Million Opportunities",
            ["dpmo", "defects_per_million"]),
    Concept("specification-limit", "quality",
            "Product specification upper/lower limits",
            ["specification_limit", "spec_limit", "usl", "lsl",
             "upper_spec_limit", "lower_spec_limit", "tolerance"]),
    Concept("calibration-record", "quality",
            "Instrument calibration record",
            ["calibration_record", "calibration", "cal_record",
             "cal_id", "calibration_date"]),
    Concept("certificate-of-analysis", "quality",
            "Certificate of Analysis for material or product lot",
            ["certificate_of_analysis", "coa", "cert_of_analysis",
             "analysis_certificate"]),
]

# ============================================================================
# Inventory Operations (ISA-95 Part 3 — Inventory, Purdue Level 3)
# ============================================================================
# Inventory operations cover warehouse management, material movement,
# and stock tracking. Typically managed by WMS or ERP inventory modules.

_INVENTORY_CONCEPTS = [
    Concept("inventory-location", "inventory",
            "Storage location (warehouse, bin, rack, shelf)",
            ["inventory_location", "storage_location", "bin",
             "rack", "shelf", "warehouse_location", "sloc"]),
    Concept("inventory-quantity", "inventory",
            "Quantity of material at a location",
            ["inventory_quantity", "qty_on_hand", "stock_qty",
             "available_qty", "on_hand"]),
    Concept("inventory-status", "inventory",
            "Material status (available, quarantine, rejected, blocked)",
            ["inventory_status", "stock_status", "material_status",
             "lot_status", "qa_status"]),
    Concept("inventory-movement", "inventory",
            "Record of material transfer between locations",
            ["inventory_movement", "stock_movement", "transfer",
             "material_transfer", "goods_movement"]),
    Concept("goods-receipt", "inventory",
            "Inbound material receipt",
            ["goods_receipt", "gr", "receiving", "receipt",
             "inbound_receipt", "grn"]),
    Concept("goods-issue", "inventory",
            "Outbound material consumption or shipment",
            ["goods_issue", "gi", "issue", "material_issue",
             "consumption"]),
    Concept("stock-count", "inventory",
            "Physical inventory count",
            ["stock_count", "physical_count", "cycle_count",
             "inventory_count", "pi_count"]),
    Concept("wip-quantity", "inventory",
            "Work-in-progress quantity",
            ["wip_quantity", "wip", "wip_qty", "in_process_qty"]),
    Concept("finished-goods-quantity", "inventory",
            "Finished goods inventory level",
            ["finished_goods_quantity", "fg_qty", "finished_goods",
             "fg_inventory"]),
    Concept("safety-stock-level", "inventory",
            "Minimum stock threshold",
            ["safety_stock_level", "safety_stock", "min_stock",
             "reorder_point", "min_qty"]),
]

# ============================================================================
# OEE and Performance Metrics
# ============================================================================

_OEE_CONCEPTS = [
    Concept("oee", "performance",
            "Overall Equipment Effectiveness (availability × performance × quality)",
            ["oee", "overall_equipment_effectiveness"]),
    Concept("availability", "performance",
            "OEE availability component",
            ["availability", "avail", "uptime_pct",
             "equipment_availability"]),
    Concept("performance-rate", "performance",
            "OEE performance component",
            ["performance_rate", "perf_rate", "speed_rate",
             "performance_efficiency"]),
    Concept("quality-rate", "performance",
            "OEE quality component",
            ["quality_rate", "qual_rate", "quality_pct",
             "good_unit_rate"]),
    Concept("planned-production-time", "performance",
            "Scheduled production time",
            ["planned_production_time", "scheduled_time",
             "planned_time", "available_time"]),
    Concept("actual-production-time", "performance",
            "Actual time equipment was producing",
            ["actual_production_time", "run_time", "operating_time",
             "actual_time"]),
    Concept("ideal-cycle-time", "performance",
            "Theoretical best cycle time",
            ["ideal_cycle_time", "design_cycle_time",
             "theoretical_cycle_time", "nameplate_rate"]),
    Concept("good-count", "performance",
            "Count of good units produced",
            ["good_count", "good_qty", "good_units",
             "conforming_count"]),
    Concept("reject-count", "performance",
            "Count of rejected units",
            ["reject_count", "reject_qty", "rejects",
             "defective_count", "bad_count"]),
    Concept("total-count", "performance",
            "Total units produced (good + reject)",
            ["total_count", "total_qty", "total_units",
             "total_output"]),
]


# ============================================================================
# IoT / SCADA / Sensor Data (Purdue Level 1-2)
# ============================================================================
# Level 1: Sensors and actuators. Level 2: Control systems (PLC, DCS).
# Data historians collect time-series data from these levels.

_IOT_CONCEPTS = [
    Concept("sensor-reading", "iot",
            "Generic sensor measurement value",
            ["sensor_reading", "reading", "measurement", "value",
             "sensor_value", "process_value", "pv"]),
    Concept("sensor-id", "iot",
            "Unique sensor identifier",
            ["sensor_id", "sensor", "sensor_name", "instrument_id",
             "device_id", "transmitter_id"]),
    Concept("sensor-type", "iot",
            "Type of sensor (temperature, pressure, vibration, flow)",
            ["sensor_type", "measurement_type", "instrument_type",
             "device_type"]),
    Concept("tag-name", "iot",
            "SCADA/PLC tag name",
            ["tag_name", "tag", "plc_tag", "scada_tag",
             "point_name", "point_id", "pi_tag"]),
    Concept("tag-value", "iot",
            "Current value of a SCADA/PLC tag",
            ["tag_value", "current_value", "live_value",
             "real_time_value"]),
    Concept("setpoint", "iot",
            "Target value for a control loop",
            ["setpoint", "sp", "set_point", "target_value",
             "desired_value"]),
    Concept("alarm", "iot",
            "Alarm event from control system",
            ["alarm", "alarm_id", "alarm_event", "alert",
             "alarm_record"]),
    Concept("alarm-priority", "iot",
            "Alarm severity/priority level",
            ["alarm_priority", "alarm_severity", "priority_level",
             "alarm_level"]),
    Concept("alarm-state", "iot",
            "Current alarm state (active, acknowledged, cleared)",
            ["alarm_state", "alarm_status", "ack_status",
             "alarm_condition"]),
    Concept("sample-rate", "iot",
            "Data collection frequency",
            ["sample_rate", "scan_rate", "polling_interval",
             "collection_frequency", "sample_interval"]),
]

# ============================================================================
# Energy and Utilities
# ============================================================================

_ENERGY_CONCEPTS = [
    Concept("energy-consumption", "energy",
            "Energy usage measurement (kWh, BTU, MJ)",
            ["energy_consumption", "energy_usage", "kwh",
             "power_consumption", "electricity_usage"]),
    Concept("power-demand", "energy",
            "Instantaneous power draw (kW, MW)",
            ["power_demand", "power_draw", "kw", "load",
             "electrical_load"]),
    Concept("utility-type", "energy",
            "Type of utility (electricity, gas, water, steam, compressed air)",
            ["utility_type", "utility", "resource_type",
             "energy_type"]),
    Concept("utility-meter-reading", "energy",
            "Meter reading for a utility",
            ["utility_meter_reading", "meter_reading", "meter_value",
             "consumption_reading"]),
    Concept("carbon-footprint", "energy",
            "CO2 equivalent emissions",
            ["carbon_footprint", "co2", "co2e", "emissions",
             "ghg", "carbon_emissions"]),
    Concept("energy-cost", "energy",
            "Cost of energy consumed",
            ["energy_cost", "utility_cost", "power_cost",
             "electricity_cost"]),
]

# ============================================================================
# Product Lifecycle (PLM)
# ============================================================================

_PLM_CONCEPTS = [
    Concept("part-spec", "plm",
            "Part specification / engineering drawing",
            ["part_spec", "drawing", "engineering_drawing",
             "spec", "specification"]),
    Concept("part-number", "plm",
            "Engineering part number",
            ["part_number", "pn", "part_no", "item_number",
             "drawing_number"]),
    Concept("part-revision", "plm",
            "Revision/version of a part design",
            ["part_revision", "revision", "rev", "version",
             "design_revision"]),
    Concept("engineering-change-order", "plm",
            "ECO / ECN for design changes",
            ["engineering_change_order", "eco", "ecn",
             "change_order", "engineering_change"]),
    Concept("product-definition", "plm",
            "ISA-95 product definition",
            ["product_definition", "product_def", "product_id",
             "product_code"]),
    Concept("product-segment", "plm",
            "ISA-95 product segment",
            ["product_segment", "prod_segment"]),
    Concept("process-parameter", "plm",
            "Named process parameter (temperature, pressure, speed)",
            ["process_parameter", "process_param", "param",
             "parameter_name"]),
    Concept("process-parameter-value", "plm",
            "Actual value of a process parameter",
            ["process_parameter_value", "param_value",
             "actual_value", "measured_value"]),
    Concept("simulation-model", "plm",
            "Digital twin simulation model reference",
            ["simulation_model", "digital_twin", "sim_model",
             "twin_model"]),
]

# ============================================================================
# Supply Chain and Logistics (Purdue Level 4)
# ============================================================================

_SUPPLY_CHAIN_CONCEPTS = [
    Concept("supplier-id", "supply-chain",
            "Supplier/vendor identifier",
            ["supplier_id", "vendor_id", "supplier", "vendor",
             "vendor_code", "supplier_code", "vendor_number"]),
    Concept("customer-id", "supply-chain",
            "Customer identifier",
            ["customer_id", "customer", "cust_id", "customer_code",
             "customer_number", "sold_to"]),
    Concept("purchase-order", "supply-chain",
            "Purchase order for materials",
            ["purchase_order", "po", "po_number", "po_id",
             "procurement_order"]),
    Concept("sales-order", "supply-chain",
            "Customer sales order",
            ["sales_order", "so", "so_number", "customer_order",
             "order_id"]),
    Concept("shipment", "supply-chain",
            "Shipment/delivery record",
            ["shipment", "shipment_id", "delivery", "delivery_id",
             "consignment"]),
    Concept("delivery-date", "supply-chain",
            "Planned or actual delivery date",
            ["delivery_date", "ship_date", "expected_delivery",
             "promised_date", "due_date"]),
    Concept("carrier", "supply-chain",
            "Shipping carrier/logistics provider",
            ["carrier", "carrier_id", "shipper", "logistics_provider",
             "freight_carrier"]),
]

# ============================================================================
# Safety and Environmental
# ============================================================================

_SAFETY_CONCEPTS = [
    Concept("safety-incident", "safety",
            "Safety event or near-miss",
            ["safety_incident", "incident", "near_miss",
             "safety_event", "accident"]),
    Concept("safety-observation", "safety",
            "Proactive safety observation",
            ["safety_observation", "observation", "safety_audit",
             "bbs_observation"]),
    Concept("permit-to-work", "safety",
            "Work permit for hazardous activities",
            ["permit_to_work", "ptw", "work_permit",
             "hot_work_permit", "confined_space_permit"]),
    Concept("lockout-tagout", "safety",
            "LOTO record for equipment isolation",
            ["lockout_tagout", "loto", "isolation_record",
             "energy_isolation"]),
    Concept("environmental-reading", "safety",
            "Environmental measurement (emissions, noise, dust)",
            ["environmental_reading", "env_reading", "emission",
             "noise_level", "air_quality"]),
    Concept("waste-record", "safety",
            "Waste generation and disposal record",
            ["waste_record", "waste", "waste_disposal",
             "hazardous_waste"]),
]

# ============================================================================
# Weather and Environmental Conditions
# ============================================================================

_WEATHER_CONCEPTS = [
    Concept("weather-condition", "weather",
            "Current weather state (clear, rain, snow, fog)",
            ["weather_condition", "weather", "conditions",
             "sky_condition"]),
    Concept("ambient-temperature", "weather",
            "Outdoor air temperature",
            ["ambient_temperature", "ambient_temp", "outside_temp",
             "air_temperature", "outdoor_temp"]),
    Concept("humidity", "weather",
            "Relative humidity percentage",
            ["humidity", "relative_humidity", "rh", "rh_pct"]),
    Concept("wind-speed", "weather",
            "Wind speed measurement",
            ["wind_speed", "wind_velocity", "wind_mph",
             "wind_kph"]),
    Concept("wind-direction", "weather",
            "Wind direction (compass bearing)",
            ["wind_direction", "wind_dir", "wind_bearing"]),
    Concept("precipitation", "weather",
            "Rain/snow amount or rate",
            ["precipitation", "precip", "rainfall", "snowfall"]),
    Concept("visibility", "weather",
            "Visibility distance (fog, haze conditions)",
            ["visibility", "vis", "visibility_miles"]),
    Concept("heat-index", "weather",
            "Feels-like temperature (heat + humidity)",
            ["heat_index", "feels_like", "apparent_temperature"]),
    Concept("wind-chill", "weather",
            "Feels-like temperature (cold + wind)",
            ["wind_chill", "windchill", "chill_factor"]),
    Concept("uv-index", "weather",
            "UV radiation index",
            ["uv_index", "uv", "ultraviolet_index"]),
    Concept("weather-alert", "weather",
            "Severe weather warning or advisory",
            ["weather_alert", "weather_warning", "storm_warning",
             "severe_weather"]),
    Concept("lightning-proximity", "weather",
            "Lightning strike distance from site",
            ["lightning_proximity", "lightning_distance",
             "lightning_alert"]),
    Concept("air-quality-index", "weather",
            "AQI measurement for outdoor work safety",
            ["air_quality_index", "aqi", "air_quality"]),
]

# ============================================================================
# Facility and Site (ISA-95 Equipment Hierarchy — upper levels)
# ============================================================================
# ISA-95 equipment hierarchy: Enterprise > Site > Area > ...
# These represent the upper levels of the Purdue model.

_FACILITY_CONCEPTS = [
    Concept("site-id", "facility",
            "Plant/factory/site identifier (ISA-95 Site level)",
            ["site_id", "plant", "plant_id", "plant_code",
             "factory", "site", "facility_code"]),
    Concept("area-id", "facility",
            "Production area identifier (ISA-95 Area level)",
            ["area_id", "area", "production_area", "dept",
             "department", "zone"]),
    Concept("facility-id", "facility",
            "Facility identifier within a site",
            ["facility_id", "facility", "building_id",
             "campus_id"]),
    Concept("building", "facility",
            "Building identifier",
            ["building", "building_id", "bldg", "structure"]),
    Concept("floor-level", "facility",
            "Floor/level within a building",
            ["floor_level", "floor", "level", "storey"]),
    Concept("geo-location", "facility",
            "GPS coordinates or address",
            ["geo_location", "gps", "coordinates", "latitude",
             "longitude", "address"]),
]

# ============================================================================
# Traceability and Compliance
# ============================================================================

_TRACEABILITY_CONCEPTS = [
    Concept("traceability-record", "traceability",
            "Link between input lots and output lots",
            ["traceability_record", "trace_record", "genealogy_record",
             "lot_trace"]),
    Concept("genealogy", "traceability",
            "Product genealogy (parent-child lot relationships)",
            ["genealogy", "product_genealogy", "lot_genealogy",
             "as_built"]),
    Concept("audit-trail", "traceability",
            "Record of system actions for compliance",
            ["audit_trail", "audit_log", "change_log",
             "activity_log", "event_log"]),
    Concept("electronic-signature", "traceability",
            "21 CFR Part 11 electronic signature",
            ["electronic_signature", "esig", "e_signature",
             "digital_signature"]),
    Concept("regulatory-standard", "traceability",
            "Applicable regulation (GMP, ISO, FDA, OSHA)",
            ["regulatory_standard", "regulation", "standard",
             "compliance_standard"]),
    Concept("compliance-status", "traceability",
            "Compliance state (compliant, non-compliant, pending)",
            ["compliance_status", "compliance", "compliant",
             "compliance_state"]),
]


# ============================================================================
# Concept Registry — single source of truth
# ============================================================================

CONCEPTS: list[Concept] = (
    _EQUIPMENT_CONCEPTS
    + _PHYSICAL_ASSET_CONCEPTS
    + _MATERIAL_CONCEPTS
    + _PERSONNEL_CONCEPTS
    + _PRODUCTION_CONCEPTS
    + _MAINTENANCE_CONCEPTS
    + _QUALITY_CONCEPTS
    + _INVENTORY_CONCEPTS
    + _OEE_CONCEPTS
    + _IOT_CONCEPTS
    + _ENERGY_CONCEPTS
    + _PLM_CONCEPTS
    + _SUPPLY_CHAIN_CONCEPTS
    + _SAFETY_CONCEPTS
    + _WEATHER_CONCEPTS
    + _FACILITY_CONCEPTS
    + _TRACEABILITY_CONCEPTS
)
"""All canonical concepts as ``Concept`` objects."""

# Backward-compatible flat list of concept IDs (used by existing tools).
CANONICAL_CONCEPTS: list[str] = [c.id for c in CONCEPTS]

# Domain-qualified IDs for disambiguation.
QUALIFIED_CONCEPTS: list[str] = [c.qualified_id for c in CONCEPTS]

# ---- Lookup indexes (built once at import time) ----

_CONCEPT_SET: frozenset[str] = frozenset(CANONICAL_CONCEPTS)
"""O(1) membership check by simple ID."""

_QUALIFIED_SET: frozenset[str] = frozenset(QUALIFIED_CONCEPTS)
"""O(1) membership check by domain-qualified ID."""

_BY_QUALIFIED_ID: dict[str, Concept] = {c.qualified_id: c for c in CONCEPTS}
"""Lookup a Concept by its domain-qualified ID."""

_BY_DOMAIN: dict[str, list[Concept]] = {}
for _c in CONCEPTS:
    _BY_DOMAIN.setdefault(_c.domain, []).append(_c)
"""Concepts grouped by domain."""

_BARE_TO_QUALIFIED: dict[str, list[str]] = {}
for _c in CONCEPTS:
    _BARE_TO_QUALIFIED.setdefault(_c.id, []).append(_c.qualified_id)
"""Bare ID → list of domain-qualified IDs (e.g. "work-order" → ["production.work-order", "maintenance.work-order"])."""

_ALIAS_INDEX: dict[str, list[Concept]] = {}
for _c in CONCEPTS:
    for _a in _c.aliases:
        _ALIAS_INDEX.setdefault(_a.lower(), []).append(_c)
"""Reverse index: alias → list of Concept objects that claim it."""


# ============================================================================
# Public API
# ============================================================================

def is_valid_concept(concept_id: str) -> bool:
    """Check whether *concept_id* belongs to the canonical vocabulary.

    Accepts both simple IDs (``"oee"``) and domain-qualified IDs
    (``"performance.oee"``).
    """
    return concept_id in _CONCEPT_SET or concept_id in _QUALIFIED_SET


def get_concept(qualified_id: str) -> Concept | None:
    """Return a Concept by its domain-qualified ID, or ``None``."""
    return _BY_QUALIFIED_ID.get(qualified_id)


def get_concepts_by_domain(domain: str) -> list[Concept]:
    """Return all concepts in a given domain."""
    return list(_BY_DOMAIN.get(domain, []))


def get_domains() -> list[str]:
    """Return sorted list of all domain names."""
    return sorted(_BY_DOMAIN.keys())


def lookup_by_alias(alias: str) -> list[Concept]:
    """Find concepts whose curated aliases match *alias* (case-insensitive).

    Returns a list because the same alias may map to concepts in different
    domains (e.g. ``"wo"`` → production.work-order AND maintenance.work-order).
    """
    return list(_ALIAS_INDEX.get(alias.lower(), []))


def resolve_to_qualified(concept_id: str) -> list[str]:
    """Resolve a concept ID to domain-qualified ID(s).

    If *concept_id* is already qualified (contains a dot and is in the
    vocabulary), returns it as a single-element list. If it's a bare ID,
    returns all domain-qualified variants. Returns an empty list if unknown.
    """
    if concept_id in _QUALIFIED_SET:
        return [concept_id]
    return list(_BARE_TO_QUALIFIED.get(concept_id, []))


def get_all_concepts_serializable() -> list[dict]:
    """Return all concepts as dicts suitable for JSON serialization.

    Used by the ``get_canonical_concepts`` tool to send the full vocabulary
    (with aliases) to the analysis sub-agent.
    """
    return [
        {
            "id": c.id,
            "domain": c.domain,
            "qualified_id": c.qualified_id,
            "description": c.description,
            "aliases": c.aliases,
        }
        for c in CONCEPTS
    ]
