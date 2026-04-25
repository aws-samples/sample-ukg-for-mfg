# ISA-95 (IEC 62264) Reference Guide

A reference document covering the ISA-95 international standard for enterprise-control system integration in manufacturing. This document is intended as a knowledge base for answering questions about manufacturing system architecture, integration patterns, and the ISA-95 standard.

---

## Overview

ISA-95 (internationally published as IEC 62264) is the international standard for developing automated interfaces between enterprise and control systems. Originally developed by the International Society of Automation (ISA), it provides a consistent framework for integrating business systems (like ERP) with manufacturing operations systems (like MES).

The standard addresses the question: "What information needs to be exchanged between business systems and manufacturing systems, and how should that exchange be structured?"

ISA-95 is organized into multiple parts:
- Part 1: Models and Terminology — defines the functional hierarchy and key models
- Part 2: Object Model Attributes — defines the data structures for information exchange
- Part 3: Activity Models of Manufacturing Operations Management — defines Level 3 activities
- Part 4: Objects and Attributes for Manufacturing Operations Management Integration — defines MOM-to-MOM integration
- Part 5: Business-to-Manufacturing Transactions — defines transaction processing patterns

---

## The Functional Hierarchy (Levels 0-4)

ISA-95 defines a five-level functional hierarchy, often visualized as the "Automation Pyramid." Each level represents a different scope of control and planning within a manufacturing enterprise.

### Level 0 — Physical Process

The actual physical production process. This level represents the physical transformations occurring during manufacturing — chemical reactions, mechanical assembly, material forming, packaging, etc. Sensors at this level measure physical properties like temperature, pressure, flow rate, position, and vibration.

Examples: Chemical reactions in a reactor, metal being cut on a CNC machine, bottles being filled on a packaging line, welding operations on an assembly line.

### Level 1 — Sensing and Manipulation

Direct sensing and manipulation of the physical process. This level includes sensors, actuators, and basic control devices that interface directly with the physical process. Devices at this level operate in real time (milliseconds to seconds).

Examples: Temperature sensors, pressure transmitters, flow meters, proximity switches, motor drives, pneumatic valves, servo motors, vision cameras for inspection.

### Level 2 — Monitoring and Control

Real-time control and monitoring of the physical process. This level includes PLCs (Programmable Logic Controllers), DCS (Distributed Control Systems), SCADA systems, and HMI (Human-Machine Interface) panels. Control loops execute at this level, maintaining process parameters within specified ranges.

Typical systems: PLCs, DCS, SCADA, HMI, batch control systems, safety instrumented systems (SIS).

Time horizon: Seconds to minutes. Decisions are automated and real-time.

### Level 3 — Manufacturing Operations Management

Management of manufacturing operations to produce the desired products. This level coordinates and optimizes production activities across the factory floor. It bridges the gap between business planning (Level 4) and real-time process control (Level 2).

ISA-95 defines four categories of Level 3 activities:
1. Production Operations Management — scheduling, dispatching, tracking production
2. Maintenance Operations Management — scheduling and tracking maintenance activities
3. Quality Operations Management — managing quality testing and compliance
4. Inventory Operations Management — managing material movements and storage

Typical systems: MES (Manufacturing Execution System), MOM (Manufacturing Operations Management), LIMS (Laboratory Information Management System), CMMS (Computerized Maintenance Management System), WMS (Warehouse Management System).

Time horizon: Minutes to days. Shift-level and daily operational decisions.

### Level 4 — Business Planning and Logistics

Enterprise-level business activities including production planning, material procurement, customer order management, financial reporting, and strategic decision-making. This level determines what products to make, in what quantities, and when.

Typical systems: ERP (Enterprise Resource Planning), SCM (Supply Chain Management), CRM (Customer Relationship Management), PLM (Product Lifecycle Management), financial systems.

Time horizon: Days to months. Business planning and strategic decisions.


---

## Equipment Hierarchy (Role-Based)

ISA-95 defines a role-based equipment hierarchy that describes how physical assets are organized within a manufacturing enterprise. This hierarchy is independent of the functional levels and describes the physical structure of the organization.

### Enterprise
The top level of the hierarchy. Represents the entire organization or corporation. An enterprise may have multiple sites across different geographic locations. Business planning and corporate strategy occur at this level.

### Site
A physical location within the enterprise, typically a factory, plant, or campus. A site contains all the production, storage, and support facilities at one geographic location. Each site may have different systems, processes, and organizational structures.

### Area
A logical or physical grouping within a site that performs a specific type of production or operation. Examples include a machining area, assembly area, packaging area, or warehouse zone. Areas contain one or more work centers.

### Work Center
A grouping of equipment that performs a specific set of production activities. A work center typically corresponds to a production line, manufacturing cell, or process unit. Work centers are the primary unit for production scheduling and capacity planning. In process manufacturing, a work center is often called a "process cell."

### Work Unit
An individual piece of equipment or station within a work center that performs a specific operation. In discrete manufacturing, this is often called a "production unit." In process manufacturing, it may be called a "unit" (e.g., a reactor, distillation column, or mixing tank). Work units are the lowest level at which production is typically scheduled.

### Equipment Module
A functional group of equipment within a work unit that can carry out a finite number of specific process activities. Equipment modules combine sensors, actuators, and control logic to perform a defined function (e.g., a dosing module, a heating module, or a conveyor section).

### Control Module
The lowest level of the equipment hierarchy. A control module is a single device or a small group of devices that directly manipulates the process. Examples include a single valve, a motor with its drive, a sensor with its transmitter, or a simple control loop.

### Hierarchy Summary

```
Enterprise
  └── Site (Plant/Factory)
        └── Area (Production Area)
              └── Work Center (Production Line / Process Cell)
                    └── Work Unit (Machine / Station / Unit)
                          └── Equipment Module (Functional Group)
                                └── Control Module (Device)
```

---

## Activity Models

ISA-95 Part 3 defines detailed activity models for the four categories of manufacturing operations management at Level 3. Each category follows a similar pattern of activities.

### Production Operations Management

Manages the execution of production to convert raw materials into finished products. Key activities include:

- **Production Scheduling:** Creating detailed production schedules from the master production plan. Determines the sequence and timing of production orders on specific equipment.
- **Production Dispatching:** Releasing production orders to the shop floor and assigning resources (equipment, materials, personnel) to specific production tasks.
- **Production Execution Management:** Managing the actual execution of production orders, including starting, stopping, and monitoring production runs.
- **Production Tracking:** Collecting and recording data about production activities, including quantities produced, materials consumed, time spent, and equipment used.
- **Production Performance Analysis:** Analyzing production data to identify trends, calculate KPIs (OEE, yield, cycle time), and identify improvement opportunities.
- **Production Resource Management:** Managing the availability and capability of production resources including equipment, materials, and personnel.

### Maintenance Operations Management

Manages maintenance activities to keep equipment in proper operating condition. Key activities include:

- **Maintenance Scheduling:** Planning maintenance activities based on preventive maintenance schedules, predictive analytics, and corrective maintenance requests.
- **Maintenance Dispatching:** Assigning maintenance tasks to technicians and coordinating with production schedules to minimize disruption.
- **Maintenance Execution Management:** Managing the actual performance of maintenance tasks, including parts usage, labor time, and task completion.
- **Maintenance Tracking:** Recording maintenance history for each piece of equipment, including work performed, parts replaced, and time spent.
- **Maintenance Performance Analysis:** Analyzing maintenance data to calculate KPIs (MTBF, MTTR, maintenance costs) and optimize maintenance strategies.

### Quality Operations Management

Manages quality testing and compliance activities. Key activities include:

- **Quality Test Scheduling:** Planning quality inspections and tests based on production schedules, sampling plans, and regulatory requirements.
- **Quality Test Execution Management:** Managing the performance of quality tests, including sample collection, test procedures, and result recording.
- **Quality Tracking:** Recording quality data including test results, nonconformances, and corrective actions. Maintaining traceability between products and quality records.
- **Quality Performance Analysis:** Analyzing quality data to identify trends, calculate metrics (Cpk, DPMO, first pass yield), and drive improvement.

### Inventory Operations Management

Manages the movement and storage of materials throughout the manufacturing facility. Key activities include:

- **Inventory Scheduling:** Planning material movements based on production schedules and storage requirements.
- **Inventory Dispatching:** Directing material movements between storage locations, production areas, and shipping docks.
- **Inventory Tracking:** Recording material locations, quantities, lot numbers, and status (quarantine, released, rejected).
- **Inventory Performance Analysis:** Analyzing inventory data to optimize stock levels, reduce waste, and improve material flow.


---

## Integration Between Level 4 (ERP) and Level 3 (MES/MOM)

The primary focus of ISA-95 is defining the interface between Level 4 business systems and Level 3 manufacturing operations systems. The standard specifies what information crosses this boundary and in what format.

### Information Flows from Level 4 to Level 3 (Downward)

Business systems send the following types of information to manufacturing operations:

- **Production Schedule:** What products to make, in what quantities, and by when. Translated from customer orders and master production plans.
- **Product Definition:** Specifications for how to make each product, including bills of materials, process parameters, and quality requirements.
- **Resource Requirements:** Personnel qualifications, equipment capabilities, and material specifications needed for production.
- **Quality Standards:** Inspection criteria, sampling plans, and acceptance limits for quality testing.

### Information Flows from Level 3 to Level 4 (Upward)

Manufacturing operations systems send the following types of information to business systems:

- **Production Performance:** Actual quantities produced, materials consumed, labor hours, and equipment time. Used for cost accounting and inventory updates.
- **Quality Results:** Test results, nonconformance reports, and lot disposition decisions. Used for compliance reporting and customer certificates.
- **Maintenance Reports:** Equipment status, maintenance activities performed, and parts consumed. Used for asset management and cost tracking.
- **Inventory Status:** Current material locations, quantities, and status. Used for procurement planning and order fulfillment.

### Key Principle: Separation of Concerns

ISA-95 emphasizes that Level 4 systems should not directly control Level 3 operations, and Level 3 systems should not make business decisions. The boundary between levels 3 and 4 is where business intent is translated into operational execution and where operational results are reported back as business outcomes.

---

## B2MML (Business to Manufacturing Markup Language)

B2MML is an XML-based implementation of the ISA-95 standard, maintained by the MESA International organization. It provides a standardized XML schema for exchanging information between business and manufacturing systems.

B2MML defines XML schemas for the key ISA-95 objects including:
- Production Schedule and Production Performance
- Product Definition (Bill of Materials, Bill of Process)
- Equipment, Personnel, and Material information
- Quality Test Specifications and Results
- Maintenance Requests and Responses

B2MML enables interoperability between ERP and MES systems from different vendors by providing a common data format. Many ERP and MES vendors support B2MML as an integration option.

---

## Relationship Between ISA-95 and ISA-88

ISA-88 (IEC 61512) is the standard for batch control in process manufacturing. While ISA-95 addresses the integration between business and manufacturing systems, ISA-88 focuses on the control of batch manufacturing processes at Levels 1-2.

### Key Differences

| Aspect | ISA-95 | ISA-88 |
|--------|--------|--------|
| Scope | Enterprise-to-control integration (Levels 3-4) | Batch process control (Levels 1-2) |
| Focus | Information exchange between business and manufacturing | Control of batch manufacturing processes |
| Equipment Model | Enterprise → Site → Area → Work Center → Work Unit | Process Cell → Unit → Equipment Module → Control Module |
| Primary Use | MES/ERP integration | Batch recipe and process control |

### How They Work Together

ISA-88 and ISA-95 are complementary standards. ISA-95 Level 3 systems (MES) translate production orders into batch recipes that ISA-88 batch control systems execute at Level 2. The ISA-88 equipment hierarchy (Process Cell, Unit, Equipment Module, Control Module) maps to the lower levels of the ISA-95 equipment hierarchy.

In practice:
1. ERP (Level 4) creates a production order for a batch product
2. MES (Level 3, per ISA-95) schedules the batch on specific equipment and prepares the master recipe
3. Batch control system (Level 2, per ISA-88) executes the control recipe, managing phases, operations, and unit procedures
4. MES collects batch execution data and reports production performance back to ERP

---

## ISA-95 in the Context of Industry 4.0

While ISA-95 was originally developed for traditional hierarchical manufacturing architectures, its models and terminology remain relevant in modern Industry 4.0 environments. However, the strict hierarchical pyramid is evolving.

### Modern Adaptations

- **Flattened Architecture:** Cloud computing and edge devices enable direct communication between any level, reducing the need for strict hierarchical data flow. However, ISA-95's functional definitions (what each level does) remain valid even when the communication paths change.

- **Unified Namespace (UNS):** The UNS pattern uses ISA-95's equipment hierarchy as the topic structure for an MQTT broker, creating a flat, event-driven data architecture while preserving ISA-95's organizational model. Topic paths like `enterprise/site/area/line/machine/tag` directly reflect the ISA-95 hierarchy.

- **Digital Twins:** ISA-95's equipment hierarchy provides the natural structure for organizing digital twins. Each level of the hierarchy can have its own digital twin, from individual machine twins to factory-level twins.

- **Cloud MES/MOM:** Modern cloud-based MES systems still perform ISA-95 Level 3 functions but may communicate directly with Level 1-2 devices via IIoT gateways, bypassing traditional SCADA layers.

### Continued Relevance

ISA-95 remains valuable in Industry 4.0 because it provides:
- A common vocabulary for discussing manufacturing systems across vendors and technologies
- A functional framework for understanding what each system should do, regardless of how it is implemented
- A data model for structuring manufacturing information that is technology-agnostic
- A basis for organizing the Unified Namespace topic hierarchy
- A reference architecture for evaluating and selecting manufacturing software

The standard's models and terminology are widely used in manufacturing IT/OT convergence projects, digital transformation initiatives, and smart factory implementations worldwide.
