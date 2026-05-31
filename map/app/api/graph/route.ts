import { NextResponse } from "next/server";
import graphData from "@/data/market_graph.json";

export const revalidate = 60;

export async function GET() {
  return NextResponse.json(graphData);
}
