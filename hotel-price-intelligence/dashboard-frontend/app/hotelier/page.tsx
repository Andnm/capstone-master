import type { Metadata } from "next";
import { HotelierDashboard } from "@/components/hotelier-dashboard";
import { getDashboardConfig, getHotelierDashboard } from "@/lib/api";

export const metadata: Metadata = { title: "Chủ khách sạn" };
export default async function HotelierPage() {
  const config = await getDashboardConfig();
  const initialData = await getHotelierDashboard(config.defaultCheckIn);
  return <HotelierDashboard config={config} initialData={initialData} />;
}
