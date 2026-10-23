import type { Metadata } from "next";
import { HotelierDashboard } from "@/components/hotelier-dashboard";

export const metadata: Metadata = { title: "Chủ khách sạn" };
export default function HotelierPage() { return <HotelierDashboard />; }
