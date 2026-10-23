import type { Metadata } from "next";
import { ConsumerDashboard } from "@/components/consumer-dashboard";

export const metadata: Metadata = { title: "Khách đặt phòng" };
export default function ConsumerPage() { return <ConsumerDashboard />; }
