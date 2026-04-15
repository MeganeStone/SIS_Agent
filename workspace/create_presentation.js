const pptxgen = require('pptxgenjs');
const html2pptx = require('D:/seki/AI/Langchain/SIS_Agent/skills/pptx/scripts/html2pptx');

async function createAnthropicPresentation() {
    const pptx = new pptxgen();
    pptx.layout = 'LAYOUT_16x9';
    pptx.author = 'AI Assistant';
    pptx.title = 'Introduction to Anthropic';

    // Slide 1: Title
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide1_title.html', pptx);
    console.log('Slide 1 created: Title');

    // Slide 2: What is Anthropic
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide2_what_is.html', pptx);
    console.log('Slide 2 created: What is Anthropic');

    // Slide 3: Founding & History
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide3_founding.html', pptx);
    console.log('Slide 3 created: Founding & History');

    // Slide 4: Core Mission & Values
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide4_mission.html', pptx);
    console.log('Slide 4 created: Core Mission');

    // Slide 5: Claude AI Model
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide5_claude.html', pptx);
    console.log('Slide 5 created: Claude');

    // Slide 6: Key Capabilities
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide6_capabilities.html', pptx);
    console.log('Slide 6 created: Capabilities');

    // Slide 7: Constitutional AI
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide7_constitutional.html', pptx);
    console.log('Slide 7 created: Constitutional AI');

    // Slide 8: Partnerships & Investors
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide8_partnerships.html', pptx);
    console.log('Slide 8 created: Partnerships');

    // Slide 9: Use Cases
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide9_usecases.html', pptx);
    console.log('Slide 9 created: Use Cases');

    // Slide 10: Thank You
    await html2pptx('D:/seki/AI/Langchain/SIS_Agent/workspace/slide10_thankyou.html', pptx);
    console.log('Slide 10 created: Thank You');

    // Save the presentation
    await pptx.writeFile({ fileName: 'D:/seki/AI/Langchain/SIS_Agent/workspace/anthropic_presentation.pptx' });
    console.log('\n✅ Presentation created successfully: workspace/anthropic_presentation.pptx');
}

createAnthropicPresentation().catch(console.error);
