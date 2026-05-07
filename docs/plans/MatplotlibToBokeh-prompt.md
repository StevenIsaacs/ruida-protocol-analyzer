Write the following documents for the replacement of matplotlib with bokeh for responsive and interactive charts capable of handling thousands of vectors in a 2D space and structured to allow for  3D capability in the future. Features similar to those currently implemented using matplotlib should be maintained and optimized for bokeh. This is especially important for hover tooltips showing vector attributes similar to those displayed by the current implementation.

The generated documents should be:
  1. A Product Requirements Document (PRD)
  2. An Implementation Plan (IP)
  3. A Task List (TL)

# Planning and Design Requirements
The PRD should be a markdown summary which can be used to generate an Implementation Plan or detailed prompts using AI tools. Do not include implementation details in the PRD unless specified here as an explicit implementation requirement.

The generated document sequence is: Product Requirements Document (PRD), Implementation Plan (IP), and Task List (TL).

In all generated documentation, include approval sections that asks the user to approve the generated document before the next document in the sequence is generated.

Include a completion check box for each task in the Task List. A phase cannot be marked as complete until all tasks in the phase are marked as complete.

Include instructions in the Product Requirements Document (PRD) for generating the corresponding Implementation Plan (IP) once the PRD has been approved. Similarly, include instructions in the Implementation Plan (IP) for generating a Task List (TL) which can be delegated to subagents. Implementation can not begin until the Implementation Plan (IP) has been approved and the corresponding Task List (TL) has been approved by the user.

Approval can be revoked at any time by the user. If approval is revoked for any document, begin the process again with the generation of a new document. Approval for subsequent documents is contingent on the approval of previous documents in the sequence.

Do NOT begin implementation until all of the above plans have been approved and you are explicitly told to do so.

# Implementation Requirements
A key requirement is the complete removal of all matplotlib references from the source code and documentation and replaced as needed with bokeh references.

An implementation requirement is that a python virtual environment named .venv-bokeh for bokeh plotting should be created in the project root directory and all dependencies installed in that virtual environment. All invocations of python will first activate this environment. Note: This is a long-running development task and the virtual environment may need to be recreated in the future.

# Visualization Behavior
Visualization must be done with python and the charts must be rendered using a desktop GUI framework in a separate window. Include a layout of the GUI for displaying the plots and including pull downs for changing views, styles, colors, and other plot parameters. Automatically open the plots in a single window with multiple tabs.

## UI Requirements
  - The tabs should be used to switch between different views of the same data.
  - Hovering over a vector in a view should display a tooltip showing the vector attributes. The exact format of the tooltip is up to the implementer but it should be similar in format and contain the same information as the tooltip shown in the current implementation.
  - Right clicking on a vector in a view should open a context menu that allows the user to open a new tab with a copy of the current view's settings but with the selected item as the first vector displayed and all other settings the same.
  - The window should include a menu bar for file operations and settings.
  - Settings should include options for changing the plot parameters and saving the plots.
  - Each view should be capable of being displayed in its own browser tab and allow for individual settings.
  - A view should also be capable of being copied to a new tab with a copy of its settings and a link to the source data.
  - Each view should be capable of being saved as an image, vector file in SVG format, and html file.
  - Each view should also have a reset button that resets the view to the original settings.
  - Each view should also have a slider or some control to limit the number of vectors displayed to a range of values (start value and number of vectors to display, where start value is 1 based) and update the plot to display only the selected vectors.
  - Controls should be available to zoom (using the mouse scroll wheel), pan (using the right mouse button), and scale (using the left mouse button) the plots. Zooming will automatically center the view around the position of the mouse pointer.
  - A searchable pull-down displays a list of commands that were decoded and allows the user to select a command to serve as the start of a new view in a separate tab.
	  - The mouse scroll wheel is used to scroll the list inside the pull down.
	  - The user should be able to filter the vectors by type (e.g. move or cut), color, speed, and power percentage and update the plot to display only the selected vectors as they would normally appear. Unselected vectors should be displayed with a lower opacity (configurable).
	  - Hovering over a command in the pull-down should display a summary of that command in a tool-tip like display area.
	  - Hovering over an item in the summary should highlight the actual vector in the associated view(s) using some visual indication such as a highlight box or color change.
	  - Right clicking on an item in the summary should open a context menu that allows the user to open a new tab with a copy of the current view's settings but with the selected item as the first vector displayed and all other settings the same.
	  - The display of a single command should be the same as the display of a single command in the current implementation as seen in the decoded output text file. It should include the command name, the command code, the command parameters, and any other information that is currently displayed.

## Special Considerations
  - Saving Plots Natively: Use the built-in GUI save plot functionality to save as an image file (png, or jpg).
  - Terminal CLI Interaction: With the exception of --on-the-fly and command filtering, CLI behavior should be preserved. The CLI should be able to pause for input while the plot remains interactive in the GUI. When the CLI steps through commands, it should push data updates to the GUI in a thread safe manner to ensure that the GUI remains responsive.
  - Terminal CLI Commands: Update the CLI commands to work with the new plotting system.
  - On the fly: Because the Bokeh GUI plot will be launched in a separate window, the --on-the-fly option and its restrictions can be removed.
  - "Scale" Mouse Control: Use Box Zoom (drawing a rectangle to zoom into) in addition to the scroll wheel zoom. Provide a quick button to reset the view to the original settings.
  - Context Menus (Right-Click): Move bokeh panning control to the middle mouse button so that the previously described right-click context menus can be implemented.
  - Virtual Environment Setup: The creation of the .venv-bokeh virtual environment for bokeh plotting should be handled automatically using a setup script and all other bokeh related setup should be handled automatically, including the installation of all dependencies. Provide clear instructions in the Implementation Plan on how to set up the virtual environment and install the dependencies and any other setup information required.

## Testing
Provide information in the Implementation Plan on how to test the bokeh plotting system to ensure that it meets all of the requirements. This section will be used by subagents to develop the Task List. Test code will be placed in a directory named tests/bokeh-plotting.

# Additional Instructions
Ask any questions you may have while developing the PRD and I will answer them to ensure that the PRD is complete and accurate.

## File and Directory Naming Conventions
- Save the PRD as a markdown file in a subdirectory of the docs/plans directory named bokeh_plotting-<llm_name> where llm_name is the name of the LLM that generated the document. Replace any spaces in the LLM name with hyphens. The directory should be created if it doesn't exist.
- The base file name is bokeh_plotting_<DOC_TYPE>.md where <DOC_TYPE> is replaced with PRD for Product Requirements Document, IP for Implementation Plan, and TL for Task List.

