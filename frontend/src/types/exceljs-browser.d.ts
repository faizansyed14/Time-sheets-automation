// The browser build ships no dedicated types — it's the same runtime export
// shape as the main "exceljs" entry (Workbook, ValueType, etc.), so reuse
// those types for the subpath import used to avoid Node-only bundling.
declare module "exceljs/dist/exceljs.min.js" {
  import * as ExcelJS from "exceljs";
  export = ExcelJS;
}
