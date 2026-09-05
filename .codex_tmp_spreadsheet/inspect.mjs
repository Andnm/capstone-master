import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
const p="D:/MSE/CAPSTONE/outputs/crawl-data-planner-20260816/crawl_sampling_master.xlsx";
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(p));
console.log((await wb.inspect({kind:"sheet",include:"id,name",maxChars:3000})).ndjson);
console.log((await wb.inspect({kind:"table",sheetId:"CRAWL_LOG",range:"A1:Z30",include:"values,formulas",tableMaxRows:30,tableMaxCols:26,maxChars:12000})).ndjson);
