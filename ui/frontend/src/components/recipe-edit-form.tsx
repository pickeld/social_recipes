import { PlusIcon, Trash2Icon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import type {
  HowToStep,
  Instruction,
  RecipeData,
  StructuredIngredient,
} from '@/types'

function instructionLines(instructions: Instruction[] | undefined): string[] {
  const lines: string[] = []
  for (const inst of instructions ?? []) {
    if (typeof inst === 'string') {
      if (inst.trim()) lines.push(inst)
      continue
    }
    if (inst['@type'] === 'HowToSection') {
      for (const step of inst.itemListElement) {
        if (step.text.trim()) lines.push(step.text)
      }
      continue
    }
    if (inst.text.trim()) lines.push(inst.text)
  }
  return lines.length > 0 ? lines : ['']
}

function emptyIngredient(): StructuredIngredient {
  return { food: '', quantity: '', unit: '', notes: '', raw: '' }
}

function ingredientsFrom(recipe: RecipeData): StructuredIngredient[] {
  const items = recipe.recipeIngredients ?? []
  if (items.length > 0) {
    return items.map((ing) => ({
      food: ing.food ?? '',
      quantity: ing.quantity ?? '',
      unit: ing.unit ?? '',
      notes: ing.notes ?? '',
      raw: ing.raw ?? '',
    }))
  }
  const flat = recipe.recipeIngredient ?? []
  if (flat.length > 0) {
    return flat.map((line) => ({
      food: line,
      quantity: '',
      unit: '',
      notes: '',
      raw: line,
    }))
  }
  return [emptyIngredient()]
}

function nutritionChips(recipe: RecipeData): { label: string; value: string }[] {
  const nutrition = recipe.nutrition
  if (!nutrition) return []
  const chips: { label: string; value: string }[] = []
  if (nutrition.calories) chips.push({ label: 'Calories', value: nutrition.calories })
  if (nutrition.proteinContent) chips.push({ label: 'Protein', value: nutrition.proteinContent })
  if (nutrition.fatContent) chips.push({ label: 'Fat', value: nutrition.fatContent })
  if (nutrition.carbohydrateContent) chips.push({ label: 'Carbs', value: nutrition.carbohydrateContent })
  if (nutrition.fiberContent) chips.push({ label: 'Fiber', value: nutrition.fiberContent })
  if (nutrition.sugarContent) chips.push({ label: 'Sugar', value: nutrition.sugarContent })
  if (nutrition.sodiumContent) chips.push({ label: 'Sodium', value: nutrition.sodiumContent })
  return chips
}

function emitRecipe(
  recipe: RecipeData,
  name: string,
  description: string,
  recipeYield: string,
  ingredients: StructuredIngredient[],
  steps: string[],
): RecipeData {
  const recipeInstructions: HowToStep[] = steps
    .map((text) => text.trim())
    .filter(Boolean)
    .map((text) => ({ '@type': 'HowToStep', text }))
  return {
    ...recipe,
    name,
    description,
    recipeYield,
    recipeIngredients: ingredients.map((ing) => ({
      food: ing.food.trim(),
      quantity: ing.quantity.trim(),
      unit: ing.unit.trim(),
      notes: ing.notes.trim(),
      raw: ing.raw.trim(),
    })),
    recipeInstructions,
  }
}

interface RecipeEditFormProps {
  recipe: RecipeData
  onChange: (recipe: RecipeData) => void
}

export function RecipeEditForm({ recipe, onChange }: RecipeEditFormProps) {
  const name = recipe.name ?? ''
  const description = recipe.description ?? ''
  const recipeYield = recipe.recipeYield ?? ''
  const ingredients = ingredientsFrom(recipe)
  const steps = instructionLines(recipe.recipeInstructions)
  const chips = nutritionChips(recipe)

  const push = (
    nextName: string,
    nextDescription: string,
    nextYield: string,
    nextIngredients: StructuredIngredient[],
    nextSteps: string[],
  ) => {
    onChange(
      emitRecipe(recipe, nextName, nextDescription, nextYield, nextIngredients, nextSteps),
    )
  }

  return (
    <ScrollArea className="max-h-[60vh]">
      <div className="space-y-4 pr-2">
        <div className="space-y-2">
          <Label htmlFor="recipe-name">Name</Label>
          <Input
            id="recipe-name"
            value={name}
            onChange={(e) =>
              push(e.target.value, description, recipeYield, ingredients, steps)
            }
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="recipe-description">Description</Label>
          <Textarea
            id="recipe-description"
            value={description}
            rows={2}
            onChange={(e) =>
              push(name, e.target.value, recipeYield, ingredients, steps)
            }
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="recipe-yield">Yield</Label>
          <Input
            id="recipe-yield"
            value={recipeYield}
            onChange={(e) =>
              push(name, description, e.target.value, ingredients, steps)
            }
          />
        </div>

        <Separator />

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Ingredients</h3>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                push(name, description, recipeYield, [...ingredients, emptyIngredient()], steps)
              }
            >
              <PlusIcon />
              Add
            </Button>
          </div>
          <div className="space-y-2">
            {ingredients.map((ing, i) => (
              <div key={i} className="grid grid-cols-12 gap-1.5">
                <Input
                  className="col-span-3"
                  placeholder="Qty"
                  value={ing.quantity}
                  onChange={(e) => {
                    const next = ingredients.map((row, idx) =>
                      idx === i ? { ...row, quantity: e.target.value } : row,
                    )
                    push(name, description, recipeYield, next, steps)
                  }}
                />
                <Input
                  className="col-span-2"
                  placeholder="Unit"
                  value={ing.unit}
                  onChange={(e) => {
                    const next = ingredients.map((row, idx) =>
                      idx === i ? { ...row, unit: e.target.value } : row,
                    )
                    push(name, description, recipeYield, next, steps)
                  }}
                />
                <Input
                  className="col-span-4"
                  placeholder="Food"
                  value={ing.food}
                  onChange={(e) => {
                    const next = ingredients.map((row, idx) =>
                      idx === i ? { ...row, food: e.target.value } : row,
                    )
                    push(name, description, recipeYield, next, steps)
                  }}
                />
                <Input
                  className="col-span-2"
                  placeholder="Notes"
                  value={ing.notes}
                  onChange={(e) => {
                    const next = ingredients.map((row, idx) =>
                      idx === i ? { ...row, notes: e.target.value } : row,
                    )
                    push(name, description, recipeYield, next, steps)
                  }}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="col-span-1"
                  onClick={() => {
                    const next = ingredients.filter((_, idx) => idx !== i)
                    push(
                      name,
                      description,
                      recipeYield,
                      next.length > 0 ? next : [emptyIngredient()],
                      steps,
                    )
                  }}
                  title="Remove ingredient"
                >
                  <Trash2Icon />
                  <span className="sr-only">Remove ingredient</span>
                </Button>
              </div>
            ))}
          </div>
        </div>

        <Separator />

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Steps</h3>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                push(name, description, recipeYield, ingredients, [...steps, ''])
              }
            >
              <PlusIcon />
              Add
            </Button>
          </div>
          <ol className="space-y-2">
            {steps.map((step, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="mt-2 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
                  {i + 1}
                </span>
                <Textarea
                  className="min-h-16 flex-1"
                  value={step}
                  rows={2}
                  onChange={(e) => {
                    const next = steps.map((row, idx) =>
                      idx === i ? e.target.value : row,
                    )
                    push(name, description, recipeYield, ingredients, next)
                  }}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="mt-1"
                  onClick={() => {
                    const next = steps.filter((_, idx) => idx !== i)
                    push(
                      name,
                      description,
                      recipeYield,
                      ingredients,
                      next.length > 0 ? next : [''],
                    )
                  }}
                  title="Remove step"
                >
                  <Trash2Icon />
                  <span className="sr-only">Remove step</span>
                </Button>
              </li>
            ))}
          </ol>
        </div>

        {chips.length > 0 && (
          <>
            <Separator />
            <div>
              <h3 className="mb-2 text-sm font-semibold">Nutrition</h3>
              <p className="mb-2 text-xs text-muted-foreground">
                Recalculated from ingredients after you confirm.
              </p>
              <div className="flex flex-wrap gap-2">
                {chips.map(({ label, value }) => (
                  <div
                    key={label}
                    className="rounded-lg bg-muted px-2 py-1 text-xs"
                  >
                    <span className="text-muted-foreground">{label}: </span>
                    <span className="font-medium">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </ScrollArea>
  )
}
