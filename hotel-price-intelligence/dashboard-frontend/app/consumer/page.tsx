import type { Metadata } from "next";
import { ConsumerDashboard } from "@/components/consumer-dashboard";
import { getConsumerHotels, getDashboardConfig } from "@/lib/api";

export const metadata: Metadata = { title: "Khách đặt phòng" };
export default async function ConsumerPage() {
  const [config, hotels] = await Promise.all([getDashboardConfig(), getConsumerHotels()]);
  return <ConsumerDashboard config={config} hotels={hotels} />;
}
