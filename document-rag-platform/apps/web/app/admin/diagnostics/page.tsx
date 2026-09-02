import { notFound } from "next/navigation";
import { DiagnosticsPage } from "../../../features/retrieval/DiagnosticsPage";
export default function AdminDiagnosticsPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <DiagnosticsPage />;
}
