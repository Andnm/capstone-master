import type { Metadata } from "next";
import { ModelInfoView } from "@/components/model-info-view";

export const metadata: Metadata = { title: "Về mô hình" };
export default function ModelPage() { return <ModelInfoView />; }
