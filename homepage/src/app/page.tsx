import { Nav } from "@/components/Nav";
import { Hero } from "@/components/sections/Hero";
import { Problem } from "@/components/sections/Problem";
import { MissingLayer } from "@/components/sections/MissingLayer";
import { Product } from "@/components/sections/Product";
import { Differentiation } from "@/components/sections/Differentiation";
import { AISection } from "@/components/sections/AISection";
import { Preview } from "@/components/sections/Preview";
import { Evidence } from "@/components/sections/Evidence";
import { Platforms } from "@/components/sections/Platforms";
import { Access } from "@/components/sections/Access";
import { Footer } from "@/components/Footer";

export default function Page() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <Problem />
        <MissingLayer />
        <Product />
        <Differentiation />
        <AISection />
        <Preview />
        <Evidence />
        <Platforms />
        <Access />
      </main>
      <Footer />
    </>
  );
}
