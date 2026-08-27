const { Server } = require("@modelcontextprotocol/sdk/server/index.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} = require("@modelcontextprotocol/sdk/types.js");
const xmlrpc = require("xmlrpc");
const dotenv = require("dotenv");

dotenv.config();

const ODOO_URL = (process.env.ODOO_URL || "https://odoo.eruviabs.com").replace(/\/$/, "");
const ODOO_DB = process.env.ODOO_DB || "eruvia";
const ODOO_USERNAME = process.env.ODOO_USERNAME || "info@eruviabs.com";
const ODOO_API_KEY = process.env.ODOO_API_KEY || "Eruvia2026!";

const isHttps = ODOO_URL.startsWith("https://");
const urlObj = new URL(ODOO_URL);
const host = urlObj.hostname;
const port = urlObj.port ? parseInt(urlObj.port) : (isHttps ? 443 : 80);

const rpcClientOptions = {
  host: host,
  port: port,
  path: "/xmlrpc/2/common"
};

const createClient = (path) => {
  const options = { ...rpcClientOptions, path };
  return isHttps ? xmlrpc.createSecureClient(options) : xmlrpc.createClient(options);
};

let cachedUid = null;

async function authenticate() {
  if (cachedUid) return cachedUid;
  const commonClient = createClient("/xmlrpc/2/common");
  return new Promise((resolve, reject) => {
    commonClient.methodCall(
      "authenticate",
      [ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, {}],
      (error, uid) => {
        if (error) return reject(error);
        if (!uid) return reject(new Error(`Autenticación fallida en Odoo (${ODOO_URL}, DB: ${ODOO_DB}, Usuario: ${ODOO_USERNAME})`));
        cachedUid = uid;
        resolve(uid);
      }
    );
  });
}

async function executeKw(model, method, args = [], kwargs = {}) {
  const uid = await authenticate();
  const modelsClient = createClient("/xmlrpc/2/object");
  return new Promise((resolve, reject) => {
    modelsClient.methodCall(
      "execute_kw",
      [ODOO_DB, uid, ODOO_API_KEY, model, method, args, kwargs],
      (error, value) => {
        if (error) return reject(error);
        resolve(value);
      }
    );
  });
}

const server = new Server(
  {
    name: "eruvia-odoo",
    version: "1.0.0",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "create_expense",
        description: "Registra un gasto o egreso empresarial en Odoo (ej. nube, servidores, viajes, comidas, licencias) y lo contabiliza en el Estado de Resultados.",
        inputSchema: {
          type: "object",
          properties: {
            supplier_name: { type: "string", description: "Nombre del proveedor o comercio (ej. 'DigitalOcean', 'Google Cloud', 'Restaurante Madrid')" },
            description: { type: "string", description: "Concepto del gasto (ej. 'Servidor Cloud agosto', 'Almuerzo comercial')" },
            amount: { type: "number", description: "Importe en euros (ej. 12.00)" },
            category: { type: "string", description: "Categoría del gasto (ej. 'Nube / Servidores', 'Marketing', 'Comercial')" },
            date: { type: "string", description: "Fecha en formato YYYY-MM-DD (opcional)" }
          },
          required: ["supplier_name", "description", "amount"]
        }
      },
      {
        name: "get_financial_summary",
        description: "Obtiene un resumen financiero de Eruvia (Total ingresos facturados, gastos y margen neto).",
        inputSchema: { type: "object", properties: {} }
      },
      {
        name: "list_expenses",
        description: "Lista los últimos egresos o facturas de proveedores registrados en Eruvia.",
        inputSchema: {
          type: "object",
          properties: {
            limit: { type: "number", description: "Número de egresos a listar" }
          }
        }
      },
      {
        name: "search_leads",
        description: "Busca oportunidades o leads en el CRM de Eruvia European Business School.",
        inputSchema: {
          type: "object",
          properties: {
            query: { type: "string", description: "Texto a buscar en nombre, contacto o email" },
            limit: { type: "number", description: "Límite de resultados (por defecto 10)" }
          }
        }
      },
      {
        name: "create_lead",
        description: "Crea una nueva oportunidad en el CRM de Eruvia (ej. venta de cursos, masters, clientes B2B).",
        inputSchema: {
          type: "object",
          properties: {
            name: { type: "string", description: "Título de la oportunidad (ej. 'Venta 3 Cursos - Gmasivos')" },
            contact_name: { type: "string", description: "Nombre del contacto o empresa" },
            email: { type: "string", description: "Email de contacto" },
            phone: { type: "string", description: "Teléfono móvil o WhatsApp" },
            expected_revenue: { type: "number", description: "Importe estimado en euros" },
            notes: { type: "string", description: "Notas o detalles de los cursos" }
          },
          required: ["name"]
        }
      },
      {
        name: "update_lead",
        description: "Actualiza una oportunidad en el CRM (etapa, precio estimado, notas).",
        inputSchema: {
          type: "object",
          properties: {
            lead_id: { type: "number", description: "ID de la oportunidad en Odoo" },
            stage_id: { type: "number", description: "ID de la nueva etapa" },
            expected_revenue: { type: "number", description: "Nuevo importe estimado" },
            notes: { type: "string", description: "Nuevas notas de seguimiento" }
          },
          required: ["lead_id"]
        }
      },
      {
        name: "search_contacts",
        description: "Busca alumnos o empresas en la libreta de contactos de Eruvia.",
        inputSchema: {
          type: "object",
          properties: {
            query: { type: "string", description: "Nombre, email o CIF/NIF a buscar" },
            is_company: { type: "boolean", description: "True para empresas B2B, False para particulares" },
            limit: { type: "number", description: "Límite de resultados" }
          }
        }
      },
      {
        name: "create_contact",
        description: "Crea un nuevo contacto (alumno o empresa) en Odoo.",
        inputSchema: {
          type: "object",
          properties: {
            name: { type: "string", description: "Nombre del alumno o razón social de la empresa" },
            is_company: { type: "boolean", description: "True si es empresa, False si es alumno" },
            email: { type: "string", description: "Correo electrónico" },
            phone: { type: "string", description: "Teléfono de contacto" },
            vat: { type: "string", description: "NIF/CIF/DNI" },
            city: { type: "string", description: "Ciudad" }
          },
          required: ["name"]
        }
      },
      {
        name: "execute_odoo",
        description: "Herramienta universal de acceso al ERP. Permite ejecutar cualquier método ORM sobre cualquier modelo de Odoo.",
        inputSchema: {
          type: "object",
          properties: {
            model: { type: "string", description: "Modelo en Odoo (ej. 'crm.lead', 'res.partner', 'sale.order')" },
            method: { type: "string", description: "Método ORM a ejecutar (ej. 'search_read', 'create', 'write')" },
            args: { type: "array", description: "Argumentos posicionales" },
            kwargs: { type: "object", description: "Argumentos nombrados" }
          },
          required: ["model", "method"]
        }
      }
    ]
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  try {
    if (name === "create_expense") {
      const supplierName = args.supplier_name;
      const description = args.description;
      const amount = args.amount;
      const category = args.category || "Gastos Generales";
      const date = args.date || new Date().toISOString().split("T")[0];

      // Buscar o crear proveedor
      const partnerIds = await executeKw("res.partner", "search", [[["name", "ilike", supplierName]]], { limit: 1 });
      let partnerId;
      if (partnerIds && partnerIds.length > 0) {
        partnerId = partnerIds[0];
      } else {
        partnerId = await executeKw("res.partner", "create", [{
          name: supplierName,
          is_company: true,
          supplier_rank: 1,
          comment: `Proveedor registrado vía Claude MCP (${category})`
        }]);
      }

      // Buscar cuenta de gastos
      const accountIds = await executeKw("account.account", "search", [[["account_type", "=", "expense"]]], { limit: 1 });
      const accountId = accountIds && accountIds.length > 0 ? accountIds[0] : null;

      const lineVals = {
        name: `[${category}] ${description}`,
        quantity: 1,
        price_unit: amount
      };
      if (accountId) lineVals.account_id = accountId;

      const invoiceVals = {
        move_type: "in_invoice",
        partner_id: partnerId,
        invoice_date: date,
        ref: `Claude MCP: ${category}`,
        invoice_line_ids: [[0, 0, lineVals]]
      };

      const moveId = await executeKw("account.move", "create", [invoiceVals]);
      try {
        await executeKw("account.move", "action_post", [[moveId]]);
      } catch (e) {}

      return {
        content: [{
          type: "text",
          text: JSON.stringify({ status: "success", expense_id: moveId, supplier: supplierName, amount, date, category }, null, 2)
        }]
      };
    }

    if (name === "get_financial_summary") {
      const invoices = await executeKw("account.move", "search_read", [[["move_type", "=", "out_invoice"], ["state", "=", "posted"]]], { fields: ["amount_total"] });
      const bills = await executeKw("account.move", "search_read", [[["move_type", "=", "in_invoice"], ["state", "=", "posted"]]], { fields: ["amount_total"] });
      const income = (invoices || []).reduce((acc, cur) => acc + (cur.amount_total || 0), 0);
      const expenses = (bills || []).reduce((acc, cur) => acc + (cur.amount_total || 0), 0);
      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            company: "Eruvia European Business School",
            total_income_euros: Math.round(income * 100) / 100,
            total_expenses_euros: Math.round(expenses * 100) / 100,
            net_profit_euros: Math.round((income - expenses) * 100) / 100,
            invoices_count: invoices.length,
            bills_count: bills.length
          }, null, 2)
        }]
      };
    }

    if (name === "list_expenses") {
      const limit = args?.limit || 10;
      const bills = await executeKw("account.move", "search_read", [[["move_type", "=", "in_invoice"]]], {
        fields: ["id", "name", "partner_id", "invoice_date", "amount_total", "state", "ref"],
        limit,
        order: "invoice_date desc, id desc"
      });
      return { content: [{ type: "text", text: JSON.stringify(bills, null, 2) }] };
    }

    if (name === "search_leads") {
      const query = args?.query || "";
      const limit = args?.limit || 10;
      const domain = query ? ["|", "|", ["name", "ilike", query], ["contact_name", "ilike", query], ["email_from", "ilike", query]] : [];
      const fields = ["id", "name", "contact_name", "email_from", "phone", "expected_revenue", "stage_id", "create_date"];
      const results = await executeKw("crm.lead", "search_read", [domain], { fields, limit });
      return { content: [{ type: "text", text: JSON.stringify(results, null, 2) }] };
    }

    if (name === "create_lead") {
      const values = {
        name: args.name,
        contact_name: args.contact_name,
        email_from: args.email,
        phone: args.phone,
        expected_revenue: args.expected_revenue || 0.0,
        description: args.notes || "",
        type: "opportunity"
      };
      const leadId = await executeKw("crm.lead", "create", [values]);
      return { content: [{ type: "text", text: JSON.stringify({ status: "success", lead_id: leadId, message: `Oportunidad creada exitosamente en Odoo con ID: ${leadId}` }) }] };
    }

    if (name === "update_lead") {
      const values = {};
      if (args.stage_id !== undefined) values.stage_id = args.stage_id;
      if (args.expected_revenue !== undefined) values.expected_revenue = args.expected_revenue;
      if (args.notes !== undefined) values.description = args.notes;
      const success = await executeKw("crm.lead", "write", [[args.lead_id], values]);
      return { content: [{ type: "text", text: JSON.stringify({ status: "success", lead_id: args.lead_id, updated: success }) }] };
    }

    if (name === "search_contacts") {
      const query = args?.query || "";
      const limit = args?.limit || 10;
      const domain = [];
      if (query) domain.push("|", "|", ["name", "ilike", query], ["email", "ilike", query], ["vat", "ilike", query]);
      if (args?.is_company !== undefined) domain.push(["is_company", "=", args.is_company]);
      const fields = ["id", "name", "is_company", "email", "phone", "city"];
      const results = await executeKw("res.partner", "search_read", [domain], { fields, limit });
      return { content: [{ type: "text", text: JSON.stringify(results, null, 2) }] };
    }

    if (name === "create_contact") {
      const values = {
        name: args.name,
        is_company: args.is_company || false,
        email: args.email,
        phone: args.phone,
        vat: args.vat,
        city: args.city
      };
      const contactId = await executeKw("res.partner", "create", [values]);
      return { content: [{ type: "text", text: JSON.stringify({ status: "success", contact_id: contactId }) }] };
    }

    if (name === "execute_odoo") {
      const results = await executeKw(args.model, args.method, args.args || [], args.kwargs || {});
      return { content: [{ type: "text", text: JSON.stringify(results, null, 2) }] };
    }

    throw new Error(`Herramienta desconocida: ${name}`);
  } catch (err) {
    return {
      isError: true,
      content: [{ type: "text", text: `Error ejecutando ${name}: ${err.message}` }]
    };
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((error) => {
  console.error("Error iniciando servidor MCP:", error);
  process.exit(1);
});
