# Manufacturing Glossary

A reference glossary of manufacturing terminology covering production operations, maintenance, quality, supply chain, lean manufacturing, and Industry 4.0 concepts. This document is intended as a knowledge base for answering questions about manufacturing processes and terminology.

---

## General Manufacturing

**Overall Equipment Effectiveness (OEE):** A metric that measures manufacturing productivity by combining three factors: Availability × Performance × Quality. An OEE of 100% means only good parts are produced (Quality), at maximum speed (Performance), with no unplanned stops (Availability). World-class OEE is typically considered to be 85% or higher.

**Throughput:** The rate at which a manufacturing system produces finished goods over a given time period. Measured in units per hour, parts per shift, or similar. Higher throughput with consistent quality indicates better production efficiency.

**Cycle Time:** The total elapsed time from the start to the end of a process step or complete production sequence for one unit. Includes processing time, inspection, and any in-process waiting. Shorter cycle times generally indicate more efficient operations.

**Takt Time:** The maximum allowable time to produce one unit in order to meet customer demand. Calculated as Available Production Time ÷ Customer Demand. Takt time sets the pace of production — if cycle time exceeds takt time, the line cannot meet demand.

**Lead Time:** The total time from when a customer order is placed until the finished product is delivered. Includes order processing, procurement, manufacturing, and shipping time. Reducing lead time improves customer satisfaction and reduces work-in-progress inventory.

**Yield:** The percentage of products that pass quality inspection without rework or scrap. First Pass Yield (FPY) measures the percentage of units that pass through a process step correctly the first time without any rework. Rolled Throughput Yield (RTY) multiplies FPY across all process steps.

**Scrap Rate:** The percentage of materials or products that are discarded during manufacturing because they cannot meet quality specifications. Scrap represents a direct cost loss including material, labor, and machine time already invested.

**Downtime:** Any period when equipment or a production line is not operating when it is scheduled to be. Planned downtime includes scheduled maintenance and changeovers. Unplanned downtime results from equipment failures, material shortages, or other unexpected events.

**Changeover:** The process of converting a production line or machine from producing one product to another. Changeover time includes teardown, setup, adjustment, and first-article inspection. SMED (Single-Minute Exchange of Die) is a lean technique to reduce changeover time.

**Batch Processing:** A manufacturing method where products are made in groups (batches) rather than in a continuous flow. Common in pharmaceutical, chemical, and food manufacturing. Each batch is tracked for quality and traceability purposes.

**Continuous Manufacturing:** A production method where materials flow through the process without interruption. Common in chemical, petroleum, and paper manufacturing. Offers higher throughput but less flexibility than batch processing.

**Discrete Manufacturing:** Production of distinct, countable items such as automobiles, electronics, or furniture. Each unit can be individually identified and tracked through the production process.

**Work Order:** A formal document authorizing and directing the production of a specific quantity of a product. Contains information about materials needed, operations to perform, due dates, and routing through work centers.

**Bill of Materials (BOM):** A comprehensive list of raw materials, components, sub-assemblies, and quantities needed to manufacture a finished product. BOMs can be single-level (one tier) or multi-level (showing nested sub-assemblies). An accurate BOM is essential for procurement, costing, and production planning.

**Routing:** The sequence of operations and work centers that a product must pass through during manufacturing. Each routing step specifies the work center, operation, setup time, and run time per unit.

**Work Center:** A specific production area where manufacturing operations are performed. Can be a single machine, a group of machines, or a manual assembly station. Work centers have defined capacity, labor requirements, and cost rates.

**Capacity Planning:** The process of determining the production capacity needed to meet changing demand. Includes rough-cut capacity planning (long-term) and capacity requirements planning (short-term). Helps identify bottlenecks before they cause delivery delays.

**Bottleneck:** The process step or resource that limits the overall throughput of a production system. The bottleneck determines the maximum output rate of the entire line. Theory of Constraints (TOC) focuses on identifying and managing bottlenecks.

**Production Schedule:** A detailed plan specifying what products to make, in what quantities, and when. Balances customer demand, available capacity, material availability, and due dates. Often managed through MRP/ERP systems.


---

## Maintenance

**Preventive Maintenance (PM):** Scheduled maintenance activities performed at predetermined intervals to reduce the likelihood of equipment failure. Includes tasks like lubrication, filter replacement, belt inspection, and calibration. PM schedules are typically based on time intervals or usage counts.

**Predictive Maintenance (PdM):** A condition-based maintenance strategy that uses sensor data and analytics to predict when equipment will fail, allowing maintenance to be scheduled just before failure occurs. Techniques include vibration analysis, thermal imaging, oil analysis, and machine learning on sensor data.

**Corrective Maintenance (CM):** Maintenance performed after a failure has occurred to restore equipment to working condition. Also called reactive or breakdown maintenance. While sometimes unavoidable, excessive corrective maintenance indicates inadequate preventive programs.

**Mean Time Between Failures (MTBF):** The average time between equipment failures, calculated as Total Operating Time ÷ Number of Failures. A higher MTBF indicates more reliable equipment. Used to plan spare parts inventory and maintenance schedules.

**Mean Time To Repair (MTTR):** The average time required to repair a failed piece of equipment and return it to service. Calculated as Total Repair Time ÷ Number of Repairs. Lower MTTR indicates more efficient maintenance operations.

**Mean Time To Failure (MTTF):** Similar to MTBF but used for non-repairable components. Represents the average lifespan of a component before it fails and must be replaced.

**Computerized Maintenance Management System (CMMS):** Software that manages maintenance operations including work order tracking, preventive maintenance scheduling, spare parts inventory, equipment history, and maintenance labor management. Examples include IBM Maximo, SAP PM, and Fiix.

**Work Order (Maintenance):** A document that authorizes and tracks a specific maintenance task. Contains information about the equipment, problem description, assigned technician, parts needed, priority, and completion status.

**Total Productive Maintenance (TPM):** A holistic approach to maintenance that aims for zero breakdowns, zero defects, and zero accidents. Involves operators in basic maintenance tasks (autonomous maintenance) and focuses on eliminating the "six big losses" that reduce equipment effectiveness.

**Reliability-Centered Maintenance (RCM):** A systematic process for determining the most effective maintenance strategy for each piece of equipment based on its failure modes, consequences of failure, and criticality to operations.

**Asset Lifecycle Management:** The practice of managing physical assets from acquisition through operation, maintenance, and eventual disposal. Aims to optimize total cost of ownership while maintaining required performance and safety levels.

---

## Quality

**Statistical Process Control (SPC):** A method of quality control that uses statistical techniques to monitor and control a manufacturing process. SPC uses control charts to detect process variations and distinguish between common cause variation (inherent to the process) and special cause variation (assignable to specific factors).

**Six Sigma:** A data-driven methodology for eliminating defects and reducing process variation. Uses the DMAIC framework (Define, Measure, Analyze, Improve, Control) for existing processes and DMADV (Define, Measure, Analyze, Design, Verify) for new processes. A Six Sigma process produces no more than 3.4 defects per million opportunities.

**Defects Per Million Opportunities (DPMO):** A metric that normalizes defect counts by the number of opportunities for defects. Calculated as (Number of Defects × 1,000,000) ÷ (Number of Units × Opportunities per Unit). Allows comparison across different products and processes.

**Process Capability Index (Cpk):** A statistical measure of how well a process meets specification limits. A Cpk of 1.0 means the process just barely meets specifications. A Cpk of 1.33 or higher is generally considered capable. A Cpk of 2.0 indicates a Six Sigma process.

**Control Chart:** A graph used in SPC to plot process data over time with a center line (mean) and upper and lower control limits (typically ±3 standard deviations). Points outside control limits or non-random patterns indicate the process is out of statistical control.

**Root Cause Analysis (RCA):** A systematic investigation method to identify the fundamental cause of a problem rather than just addressing symptoms. Common techniques include 5 Whys, Fishbone (Ishikawa) diagrams, and Fault Tree Analysis.

**Corrective and Preventive Action (CAPA):** A systematic approach to investigating, correcting, and preventing quality problems. Corrective actions address existing nonconformances. Preventive actions address potential nonconformances before they occur. Required by ISO 9001 and FDA regulations.

**Nonconformance Report (NCR):** A document that records a deviation from specifications, standards, or procedures. NCRs trigger investigation, disposition (scrap, rework, use-as-is), and corrective action processes.

**Inspection:** The examination of materials, components, or finished products to verify they meet specified requirements. Can be performed at incoming (receiving), in-process, or final stages. Methods include visual inspection, dimensional measurement, and functional testing.

**Calibration:** The process of comparing a measurement instrument against a known standard and adjusting it to ensure accuracy. Calibration must be performed at regular intervals and is traceable to national or international standards (NIST in the US).

**ISO 9001:** The international standard for quality management systems (QMS). Specifies requirements for organizations to demonstrate their ability to consistently provide products and services that meet customer and regulatory requirements. Requires documented processes, internal audits, and continuous improvement.

---

## Supply Chain and Inventory

**Material Requirements Planning (MRP):** A production planning and inventory control system that calculates material needs based on the production schedule, bill of materials, and current inventory levels. MRP determines what to order, how much, and when.

**Enterprise Resource Planning (ERP):** Integrated business management software that connects all aspects of an organization including manufacturing, finance, HR, supply chain, and customer management. Major ERP vendors include SAP, Oracle, and Microsoft Dynamics.

**Just-In-Time (JIT):** A production strategy that produces items only as they are needed, minimizing inventory holding costs. Materials arrive at the production line exactly when needed. Requires reliable suppliers, consistent quality, and flexible production systems.

**Kanban:** A visual scheduling system that controls the flow of materials and work-in-progress. Uses cards, bins, or electronic signals to trigger production or material replenishment only when downstream processes consume inventory. A key component of lean manufacturing and JIT production.

**Safety Stock:** Extra inventory held as a buffer against uncertainty in demand or supply. Calculated based on demand variability, lead time variability, and desired service level. Too much safety stock ties up capital; too little risks stockouts.

**Lead Time (Procurement):** The time from placing a purchase order to receiving the materials. Includes supplier processing time, manufacturing time, and shipping time. Accurate lead time data is essential for MRP calculations.

**Inventory Turns:** A measure of how many times inventory is sold and replaced over a period. Calculated as Cost of Goods Sold ÷ Average Inventory Value. Higher turns indicate more efficient inventory management.

**Work-In-Progress (WIP):** Materials and components that have entered the production process but are not yet finished goods. High WIP levels increase lead times, floor space requirements, and the risk of quality issues going undetected.

**Finished Goods Inventory (FGI):** Completed products that are ready for sale or shipment to customers. FGI levels balance customer service (immediate availability) against holding costs and obsolescence risk.

**Supply Chain Visibility:** The ability to track materials, components, and products as they move through the supply chain from raw material suppliers to end customers. Enabled by technologies like RFID, IoT sensors, and blockchain.


---

## Lean Manufacturing

**Lean Manufacturing:** A systematic approach to minimizing waste within a manufacturing system while maximizing productivity and value to the customer. Originated from the Toyota Production System (TPS). Core principles include identifying value, mapping the value stream, creating flow, establishing pull, and pursuing perfection.

**5S:** A workplace organization methodology consisting of five steps: Sort (remove unnecessary items), Set in Order (organize remaining items), Shine (clean the workspace), Standardize (create consistent procedures), and Sustain (maintain the discipline). 5S creates a foundation for other lean improvements.

**Kaizen:** A philosophy of continuous incremental improvement involving all employees. Kaizen events (or blitzes) are focused, short-term improvement projects typically lasting 3-5 days that target a specific process or area.

**Value Stream Mapping (VSM):** A lean tool that visually maps the flow of materials and information from raw material to customer delivery. Identifies value-adding and non-value-adding steps, lead times, and inventory levels. Used to design a future-state map with reduced waste.

**Muda (Waste):** Any activity that consumes resources but does not add value from the customer's perspective. The seven wastes (TIMWOOD) are: Transportation, Inventory, Motion, Waiting, Overproduction, Over-processing, and Defects. An eighth waste, unused talent, is sometimes added.

**Poka-Yoke (Error-Proofing):** A mechanism or design feature that prevents mistakes or makes them immediately obvious. Examples include asymmetric connectors that can only be inserted one way, sensors that detect missing components, and software validation checks.

**Gemba:** A Japanese term meaning "the actual place" — the shop floor or location where value-creating work happens. Gemba walks involve managers going to the production floor to observe processes, engage with workers, and identify improvement opportunities firsthand.

**Andon:** A visual management system that signals the status of a production line or process. Typically uses colored lights (green = normal, yellow = attention needed, red = stopped) to alert supervisors and support teams to problems in real time.

**Heijunka (Production Leveling):** A technique for smoothing production volume and mix over time to reduce unevenness (mura). Instead of producing large batches of one product, heijunka sequences smaller quantities of different products to match average demand patterns.

**Standard Work:** Documented best practices for performing a task, including the sequence of steps, timing, and quality checks. Standard work provides a baseline for improvement and ensures consistency across shifts and operators.

---

## Industry 4.0 and Smart Manufacturing

**Digital Twin:** A virtual replica of a physical asset, process, or system that is continuously updated with real-time data from sensors and other sources. Used for simulation, monitoring, predictive maintenance, and optimization. Can represent individual machines, production lines, or entire factories.

**Industrial Internet of Things (IIoT):** The application of IoT technology in industrial settings. Connects machines, sensors, and control systems to collect and exchange data for monitoring, analytics, and automation. Enables condition monitoring, predictive maintenance, and real-time production visibility.

**Edge Computing:** Processing data near the source of generation (at the "edge" of the network) rather than sending all data to a centralized cloud or data center. In manufacturing, edge devices process sensor data locally for real-time control decisions while sending aggregated data to the cloud for analytics.

**SCADA (Supervisory Control and Data Acquisition):** A system architecture for monitoring and controlling industrial processes. SCADA systems collect data from sensors and PLCs, display it on operator screens (HMIs), and enable remote control of equipment. Common in utilities, oil and gas, and large-scale manufacturing.

**PLC (Programmable Logic Controller):** An industrial computer designed to control manufacturing processes. PLCs read inputs from sensors and switches, execute programmed logic, and control outputs to actuators, motors, and valves. They are ruggedized for harsh industrial environments and operate in real time.

**HMI (Human-Machine Interface):** A device or software application that provides a visual interface for operators to monitor and interact with industrial control systems. HMIs display process data, alarms, and trends, and allow operators to adjust setpoints and acknowledge alerts.

**MES (Manufacturing Execution System):** Software that manages and monitors work-in-progress on the factory floor. MES bridges the gap between ERP (business planning) and the shop floor (process control). Functions include production scheduling, quality management, performance analysis, and traceability. Operates at ISA-95 Level 3.

**MOM (Manufacturing Operations Management):** A broader term than MES that encompasses all Level 3 activities defined by ISA-95, including production operations, maintenance operations, quality operations, and inventory operations management.

**OPC UA (Open Platform Communications Unified Architecture):** A machine-to-machine communication protocol for industrial automation. Provides secure, reliable, platform-independent data exchange between devices from different vendors. Widely adopted in European manufacturing and Industry 4.0 initiatives.

**MQTT (Message Queuing Telemetry Transport):** A lightweight messaging protocol designed for constrained devices and low-bandwidth networks. Widely used in IIoT for publishing sensor data to a central broker. Uses a publish/subscribe model with topic hierarchies.

**Unified Namespace (UNS):** An architectural pattern that creates a single, centralized source of truth for all operational data in a manufacturing enterprise. Typically implemented using an MQTT broker with a hierarchical topic structure following ISA-95 levels. Enables real-time data sharing across systems without point-to-point integrations.

**Digital Thread:** A communication framework that connects data flows across the entire product lifecycle — from design and engineering through manufacturing, quality, supply chain, and field service. Provides full traceability and context for any piece of manufacturing data by linking it to related information across systems.

**Asset Administration Shell (AAS):** A standardized digital representation of an asset (machine, component, or product) as defined by the Plattform Industrie 4.0 initiative. Provides a vendor-neutral way to describe asset properties, capabilities, and interfaces. The digital twin implementation standard for Industry 4.0 in Europe.

**Cyber-Physical System (CPS):** A system that integrates computation, networking, and physical processes. In manufacturing, CPS combines sensors, actuators, controllers, and software to create intelligent systems that can monitor, analyze, and optimize physical operations autonomously.

---

## Regulatory and Compliance

**GMP (Good Manufacturing Practice):** Regulations that ensure products are consistently produced and controlled according to quality standards. Required in pharmaceutical, food, and medical device manufacturing. Covers facilities, equipment, personnel, documentation, and production controls.

**FDA 21 CFR Part 11:** US FDA regulation that defines criteria for electronic records and electronic signatures to be considered trustworthy and equivalent to paper records. Requires audit trails, access controls, and validation of computerized systems used in regulated manufacturing.

**Traceability:** The ability to track the history, application, and location of a product and its components through the entire supply chain and manufacturing process. Essential for recalls, quality investigations, and regulatory compliance. Requires unique identification (lot numbers, serial numbers) and recorded process data.

**Lot/Batch Tracking:** The practice of assigning unique identifiers to groups of products manufactured together under the same conditions. Enables traceability for quality investigations and targeted recalls. Required in pharmaceutical, food, and aerospace manufacturing.
